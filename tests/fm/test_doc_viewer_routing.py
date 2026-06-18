"""F3 on a PDF/office file routes through the converter to the Markdown viewer,
and falls back to the hex viewer on conversion failure."""

import zipfile

import dunders.app as app_mod
from dunders.app import DundersApp
from dunders.fm.doc_converter import ConvertError
from dunders.fm.hex_viewer import HexViewerContent
from dunders.fm.markdown_viewer import MarkdownViewerContent
from dunders.windowing import Desktop


def _windows(app):
    return list(app.query_one(Desktop).windows)


async def _drain(pilot):
    # Let the conversion worker run and the call_from_thread finish callback land.
    for _ in range(5):
        await pilot.pause()


async def test_pdf_opens_in_markdown_viewer(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(app_mod, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(
        app_mod, "convert_to_markdown", lambda source, name: "# Hello\n\nfrom pdf\n"
    )
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        app._open_editor_window(pdf, read_only=True)
        await _drain(pilot)
        mds = [w for w in _windows(app) if isinstance(w.content, MarkdownViewerContent)]
        assert len(mds) == 1


async def test_conversion_failure_falls_back_to_hex(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    def _boom(source, name):
        raise ConvertError("nope")

    monkeypatch.setattr(app_mod, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(app_mod, "convert_to_markdown", _boom)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        app._open_editor_window(pdf, read_only=True)
        await _drain(pilot)
        hexes = [w for w in _windows(app) if isinstance(w.content, HexViewerContent)]
        assert len(hexes) == 1


async def test_member_pdf_opens_in_markdown_viewer(tmp_path, monkeypatch):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("doc.pdf", b"%PDF-1.4 fake")
    monkeypatch.setattr(app_mod, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(
        app_mod, "convert_to_markdown", lambda source, name: "# Member\n\nok\n"
    )
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app._active_panel()
        panel.refresh_listing()
        panel.cursor = next(
            i for i, e in enumerate(panel.entries) if e.name == "a.zip"
        )
        panel.activate()  # enter the zip
        member = next(e for e in panel.entries if e.name == "doc.pdf")
        app._open_member_view(member)
        await _drain(pilot)
        mds = [w for w in _windows(app) if isinstance(w.content, MarkdownViewerContent)]
        assert len(mds) == 1
