import pytest
from textual.app import App
from textual.widgets import MarkdownViewer

from dunders.fm.image_viewer import PILLOW_AVAILABLE, _ToolbarButton
from dunders.fm.markdown_viewer import (
    MarkdownViewerContent,
    _InlineImage,
    looks_markdown,
    split_markdown_blocks,
)

# Minimal PNG magic so _resolve_image's sniff_image() accepts the file
# without needing Pillow to decode it (used by the pure block-split tests).
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8


def _make_real_png(path, size=(8, 8), color=(200, 50, 50)):
    from PIL import Image

    Image.new("RGB", size, color).save(path)


class TestLooksMarkdown:
    def test_md(self):
        assert looks_markdown("README.md") is True

    def test_markdown_uppercase(self):
        assert looks_markdown("NOTES.MARKDOWN") is True

    def test_variants(self):
        assert looks_markdown("a.mdown") is True
        assert looks_markdown("a.mkd") is True

    def test_not_markdown(self):
        assert looks_markdown("a.txt") is False
        assert looks_markdown("a.rst") is False
        assert looks_markdown("noext") is False


class TestSplitMarkdownBlocks:
    def test_no_images_single_md_block(self, tmp_path):
        segs = split_markdown_blocks("# Hi\n\nsome text\n", tmp_path)
        assert [s.kind for s in segs] == ["md"]

    def test_standalone_local_image_becomes_img(self, tmp_path):
        (tmp_path / "pic.png").write_bytes(_PNG_MAGIC)
        src = "# Title\n\n![cat](pic.png)\n\nafter\n"
        segs = split_markdown_blocks(src, tmp_path)
        assert [s.kind for s in segs] == ["md", "img", "md"]
        assert segs[1].text == "cat"
        assert segs[1].path == tmp_path / "pic.png"

    def test_url_encoded_and_titled_src(self, tmp_path):
        (tmp_path / "my pic.png").write_bytes(_PNG_MAGIC)
        segs = split_markdown_blocks('![a](my%20pic.png "title")\n', tmp_path)
        assert [s.kind for s in segs] == ["img"]
        assert segs[0].path == tmp_path / "my pic.png"

    def test_remote_image_stays_md(self, tmp_path):
        segs = split_markdown_blocks("![x](https://e.com/a.png)\n", tmp_path)
        assert [s.kind for s in segs] == ["md"]

    def test_missing_image_stays_md(self, tmp_path):
        segs = split_markdown_blocks("![x](nope.png)\n", tmp_path)
        assert [s.kind for s in segs] == ["md"]

    def test_non_image_file_stays_md(self, tmp_path):
        (tmp_path / "a.png").write_text("not really an image")
        segs = split_markdown_blocks("![x](a.png)\n", tmp_path)
        assert [s.kind for s in segs] == ["md"]

    def test_base_dir_none_keeps_text(self, tmp_path):
        (tmp_path / "pic.png").write_bytes(_PNG_MAGIC)
        segs = split_markdown_blocks(f"![x]({tmp_path / 'pic.png'})\n", None)
        assert [s.kind for s in segs] == ["md"]

    def test_inline_image_in_paragraph_stays_md(self, tmp_path):
        (tmp_path / "pic.png").write_bytes(_PNG_MAGIC)
        segs = split_markdown_blocks("see ![x](pic.png) here\n", tmp_path)
        assert [s.kind for s in segs] == ["md"]


_SRC = "# Title\n\nHello **world**.\n\n## Section\n\n- one\n- two\n"


class _Host(App):
    def compose(self):
        yield MarkdownViewerContent(text=_SRC, display_name="doc.md")


class TestMarkdownViewerContent:
    async def test_renders_and_reports_subtitle(self):
        app = _Host()
        async with app.run_test():
            content = app.query_one(MarkdownViewerContent)
            assert isinstance(content.viewer, MarkdownViewer)
            assert content.has_images is False
            assert content.raw_mode is False
            assert "RENDERED" in content.window_subtitle
            assert content.window_title == "MD: doc.md"

    async def test_toggle_raw_swaps_visible_surface(self):
        app = _Host()
        async with app.run_test():
            content = app.query_one(MarkdownViewerContent)
            content._toggle_raw()
            assert content.raw_mode is True
            assert content.document.display is False
            assert "RAW" in content.window_subtitle
            content._toggle_raw()
            assert content.raw_mode is False
            assert content.document.display is True

    async def test_toggle_toc(self):
        app = _Host()
        async with app.run_test():
            content = app.query_one(MarkdownViewerContent)
            assert content.show_toc is False
            content._toggle_toc()
            assert content.show_toc is True
            assert content.viewer.show_table_of_contents is True
            # TOC toggle is a no-op while viewing raw source.
            content._toggle_raw()
            content._toggle_toc()
            assert content.show_toc is True

    async def test_rendered_surface_is_focusable_on_mount(self):
        # Regression: the plain MarkdownViewer defaults to can_focus=False, so
        # focusing it on mount was a no-op and arrow/wheel scroll did nothing
        # until the user clicked the document first.
        app = _Host()
        async with app.run_test():
            content = app.query_one(MarkdownViewerContent)
            assert content.document.can_focus is True
            assert content.document.has_focus is True

    def test_from_bytes_lossy_decode(self):
        content = MarkdownViewerContent.from_bytes("x.md", b"# Hi\n\xff\xfe rest")
        assert content.window_title == "MD: x.md"
        assert "# Hi" in content._source

    def test_get_commands_expose_hotkeys(self):
        content = MarkdownViewerContent(text=_SRC, display_name="doc.md")
        ids = {c.id: c.hotkey for c in content.get_commands()}
        assert ids["markdown.toggle_raw"] == "t"
        assert ids["markdown.toggle_toc"] == "c"

    async def test_text_only_doc_has_no_inline_images(self):
        app = _Host()
        async with app.run_test():
            assert len(app.query(_InlineImage)) == 0

    async def test_text_doc_shows_both_toolbar_buttons(self):
        app = _Host()
        async with app.run_test():
            labels = {b.label.plain for b in app.query(_ToolbarButton)}
            assert "[ Raw ]" in labels
            assert "[ Contents ]" in labels

    async def test_raw_button_reflows_to_fit_longer_label(self):
        # Regression: set_label must reflow the width:auto pill, otherwise the
        # longer "[ Rendered ]" stays clipped to the old "[ Raw ]" width.
        app = _Host()
        async with app.run_test() as pilot:
            content = app.query_one(MarkdownViewerContent)
            content._toggle_raw()
            await pilot.pause()
            assert content._raw_btn.label.plain == "[ Rendered ]"
            # Pill is " [ Rendered ] " (14 cells); the region must fit it.
            assert content._raw_btn.region.width >= 14


@pytest.mark.skipif(not PILLOW_AVAILABLE, reason="needs Pillow to decode images")
class TestInlineImages:
    async def test_local_image_uses_composed_renderer(self, tmp_path):
        _make_real_png(tmp_path / "pic.png")
        md = tmp_path / "doc.md"
        md.write_text("# Title\n\n![cat](pic.png)\n\nafter the image\n")

        class _ImgHost(App):
            def compose(self):
                yield MarkdownViewerContent(file_path=md)

        app = _ImgHost()
        async with app.run_test() as pilot:
            await pilot.pause()
            content = app.query_one(MarkdownViewerContent)
            assert content.has_images is True
            assert content.image_count == 1
            assert content.viewer is None  # composed renderer, not MarkdownViewer
            assert "1 image" in content.window_subtitle
            assert len(app.query(_InlineImage)) == 1
            # No aggregated TOC for the composed renderer → no dead button.
            labels = {b.label.plain for b in app.query(_ToolbarButton)}
            assert labels == {"[ Raw ]"}

    async def test_toggle_toc_noop_without_viewer(self, tmp_path):
        _make_real_png(tmp_path / "pic.png")
        md = tmp_path / "doc.md"
        md.write_text("![cat](pic.png)\n")

        class _ImgHost(App):
            def compose(self):
                yield MarkdownViewerContent(file_path=md)

        app = _ImgHost()
        async with app.run_test():
            content = app.query_one(MarkdownViewerContent)
            content._toggle_toc()  # must not raise though there is no MarkdownViewer
            assert content.show_toc is False

    async def test_inline_image_renders_ascii_text(self, tmp_path):
        _make_real_png(tmp_path / "pic.png", size=(16, 16))
        md = tmp_path / "doc.md"
        md.write_text("![cat](pic.png)\n")

        class _ImgHost(App):
            def compose(self):
                yield MarkdownViewerContent(file_path=md)

        app = _ImgHost()
        async with app.run_test() as pilot:
            await pilot.pause()
            img = app.query_one(_InlineImage)
            plain = img.art.plain
            # Non-empty ASCII grid plus the alt caption.
            assert "cat" in plain
            assert len(plain.strip()) > 0
