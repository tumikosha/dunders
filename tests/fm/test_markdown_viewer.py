import pytest
from textual.app import App
from textual.widgets import MarkdownViewer, Static

from dunders.fm.image_viewer import PILLOW_AVAILABLE, _ToolbarButton
from dunders.fm.line_source import TextSource
from dunders.fm.markdown_viewer import (
    _HUGE_CAP,
    _MAX_BLOCKS,
    _RICH_RENDER_HARD_CAP,
    MarkdownViewerContent,
    _InlineImage,
    _LazyTextView,
    estimate_blocks,
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

    async def test_raw_view_does_not_parse_source_as_markup(self):
        # Regression: the raw Static must render the source literally. Markdown
        # text containing Textual-markup-like tokens (e.g. "[/]") otherwise
        # raises MarkupError and crashes the app when Raw is shown.
        class Host(App):
            def compose(self):
                yield MarkdownViewerContent(
                    text="# T\n\nbad [/] token and [link](x)\n", display_name="m.md"
                )

        app = Host()
        async with app.run_test() as pilot:
            content = app.query_one(MarkdownViewerContent)
            content._toggle_raw()       # show the raw view
            await pilot.pause()         # force a render — crashes pre-fix
            assert content.raw_mode is True
            raw_static = content._raw_view.query_one(Static)
            assert raw_static._render_markup is False

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
        assert "# Hi" in content._source_text

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


class TestEstimateBlocks:
    def test_empty_is_zero_or_one(self):
        assert estimate_blocks("") <= 1

    def test_counts_each_paragraph(self):
        src = "para one\n\npara two\n\npara three\n"
        assert estimate_blocks(src) >= 3

    def test_counts_each_list_item(self):
        src = "- a\n- b\n- c\n- d\n"
        assert estimate_blocks(src) >= 4

    def test_counts_table_rows(self):
        src = "| h1 | h2 |\n| -- | -- |\n| a | b |\n| c | d |\n"
        assert estimate_blocks(src) >= 4

    def test_counts_headings(self):
        src = "# H1\n\n## H2\n\n### H3\n"
        assert estimate_blocks(src) >= 3

    def test_overcounts_not_undercounts_list_heavy(self):
        # 100 list items must read as "many blocks", never as one block.
        src = "".join(f"- item {i}\n" for i in range(100))
        assert estimate_blocks(src) >= 100


class TestTierRouting:
    def test_small_doc_is_interactive(self):
        c = MarkdownViewerContent(text="# Hi\n\nshort\n", display_name="a.md")
        assert c.tier == "interactive"

    def test_block_dense_doc_is_rich(self):
        # Many blocks but under the size cap → Rich static render.
        src = "".join(f"- item {i}\n" for i in range(_MAX_BLOCKS + 50))
        assert len(src.encode()) <= _HUGE_CAP
        c = MarkdownViewerContent(text=src, display_name="dense.md")
        assert c.tier == "rich"

    def test_huge_local_file_is_lazy_without_full_read(self, tmp_path):
        p = tmp_path / "huge.md"
        p.write_text("para\n\n" * (_HUGE_CAP // 3))  # well over _HUGE_CAP
        assert p.stat().st_size > _HUGE_CAP
        c = MarkdownViewerContent(file_path=p)
        assert c.tier == "lazy"
        assert c._source_text is None  # huge file not read into memory

    async def test_lazy_tier_mounts_lazy_view(self, tmp_path):
        p = tmp_path / "huge.md"
        p.write_text("para line\n" * (_HUGE_CAP // 5))
        c = MarkdownViewerContent(file_path=p)

        class Host(App):
            def compose(self):
                yield c

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            # The huge tier opens raw: the lazy view is the raw surface, shown,
            # focused, and no rendered surface is built yet.
            assert isinstance(c._raw_view, _LazyTextView)
            assert c._raw_view.has_focus is True
            assert c.raw_mode is True
            assert c._rendered is None

    async def test_rich_tier_mounts_static(self):
        src = "".join(f"- item {i}\n" for i in range(_MAX_BLOCKS + 50))
        c = MarkdownViewerContent(text=src, display_name="dense.md")

        class Host(App):
            def compose(self):
                yield c

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query(Static)) >= 1  # Rich renderable in a Static

    async def test_lazy_toggle_renders_on_demand_and_back(self, tmp_path):
        """Huge tier opens raw; the single Raw/Rendered toggle builds the Rich
        render lazily on first flip and toggles back to the lazy view, which
        stays alive (its source is not closed by the toggle)."""
        p = tmp_path / "huge.md"
        p.write_text("para line\n" * (_HUGE_CAP // 5))
        c = MarkdownViewerContent(file_path=p)

        class Host(App):
            def compose(self):
                yield c

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            lazy = c._raw_view
            assert isinstance(lazy, _LazyTextView)
            assert c._rendered is None

            # First toggle → build & show the rendered (Rich) surface.
            c._toggle_raw()
            await pilot.pause()
            assert c.raw_mode is False
            assert c._rendered is not None
            assert not isinstance(c._rendered, _LazyTextView)
            assert c._rendered.display is True
            assert lazy.display is False
            # The lazy raw view (and its mmap source) is kept for toggling back.
            assert c._raw_view is lazy
            assert getattr(lazy, "source", None) is not None

            # Toggle back → the lazy raw view is shown again.
            c._toggle_raw()
            await pilot.pause()
            assert c.raw_mode is True
            assert lazy.display is True
            assert c._rendered.display is False

    async def test_huge_over_hard_cap_is_raw_only(self, tmp_path):
        """A file larger than the render hard cap is raw-only: no toggle button,
        and _toggle_raw is a no-op (rendering would freeze)."""
        p = tmp_path / "massive.md"
        p.write_text("x" * (_RICH_RENDER_HARD_CAP + 1024))
        c = MarkdownViewerContent(file_path=p)
        assert c.tier == "lazy"
        assert c._can_render is False

        class Host(App):
            def compose(self):
                yield c

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert c._raw_btn.is_mounted is False  # toggle button not shown
            c._toggle_raw()  # no-op
            await pilot.pause()
            assert c.raw_mode is True
            assert c._rendered is None


class TestLazyTextView:
    async def test_renders_visible_lines_only(self):
        src = TextSource("".join(f"line {i}\n" for i in range(1000)))
        view = _LazyTextView(src)

        class Host(App):
            def compose(self):
                yield view

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert view.can_focus is True
            # virtual height reflects the full line count, not the viewport
            assert view.virtual_size.height >= 1000
            strip = view.render_line(0)
            assert "line 0" in strip.text

    async def test_action_scroll_page_scrolls_down(self):
        src = TextSource("".join(f"line {i}\n" for i in range(1000)))
        view = _LazyTextView(src)

        class Host(App):
            def compose(self):
                yield view

        app = Host()
        async with app.run_test() as pilot:
            await pilot.pause()
            before = view.scroll_offset.y
            view.action_scroll_page(1)
            await pilot.pause()
            assert view.scroll_offset.y > before
