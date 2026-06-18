import pytest

from dunders.fm.doc_converter import (
    ConvertError,
    MARKITDOWN_AVAILABLE,
    OFFICE_SUFFIXES,
    convert_to_markdown,
    looks_office,
)


class TestLooksOffice:
    def test_pdf(self):
        assert looks_office("report.pdf") is True

    def test_uppercase(self):
        assert looks_office("DECK.PPTX") is True

    def test_all_suffixes(self):
        for suf in OFFICE_SUFFIXES:
            assert looks_office("file" + suf) is True

    def test_rejects_others(self):
        assert looks_office("a.txt") is False
        assert looks_office("a.csv") is False   # CSV has its own viewer
        assert looks_office("a.png") is False
        assert looks_office("noext") is False

    def test_non_str(self):
        from pathlib import Path
        assert looks_office(Path("x.docx")) is True


class _FakeResult:
    def __init__(self, text):
        self.text_content = text


def _install_fake_markitdown(monkeypatch, *, convert=None, ctor_raises=None):
    """Replace the MarkItDown symbol with a deterministic fake so the wrapper's
    own branches are tested without depending on markitdown internals (or even
    its installation). ``convert`` returns the result for both convert paths;
    ``ctor_raises`` makes the constructor raise."""
    import dunders.fm.doc_converter as dc

    class _FakeMD:
        def __init__(self, *a, **k):
            if ctor_raises is not None:
                raise ctor_raises

        def convert(self, *a, **k):
            return convert(*a, **k)

        def convert_stream(self, *a, **k):
            return convert(*a, **k)

    monkeypatch.setattr(dc, "MARKITDOWN_AVAILABLE", True)
    monkeypatch.setattr(dc, "MarkItDown", _FakeMD)


class TestConvertWrapper:
    """The wrapper logic, exercised with a fake converter (no markitdown
    needed). Covers all three ConvertError branches plus the success path."""

    def test_success_returns_text(self, monkeypatch):
        _install_fake_markitdown(monkeypatch, convert=lambda *a, **k: _FakeResult("# ok\n"))
        assert convert_to_markdown(b"data", "a.pdf") == "# ok\n"

    def test_converter_exception_wrapped(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("kaboom")

        _install_fake_markitdown(monkeypatch, convert=_boom)
        with pytest.raises(ConvertError):
            convert_to_markdown(b"data", "a.pdf")

    def test_constructor_exception_wrapped(self, monkeypatch):
        # Guards the fix that moved MarkItDown() inside the try: a constructor
        # failure must become ConvertError, not escape the worker.
        _install_fake_markitdown(monkeypatch, ctor_raises=RuntimeError("ctor"))
        with pytest.raises(ConvertError):
            convert_to_markdown(b"data", "a.pdf")

    def test_empty_output_raises(self, monkeypatch):
        _install_fake_markitdown(monkeypatch, convert=lambda *a, **k: _FakeResult(""))
        with pytest.raises(ConvertError):
            convert_to_markdown(b"data", "a.pdf")

    def test_convert_without_extra_raises(self, monkeypatch):
        import dunders.fm.doc_converter as dc
        monkeypatch.setattr(dc, "MARKITDOWN_AVAILABLE", False)
        with pytest.raises(ConvertError):
            convert_to_markdown(b"x", "a.pdf")


@pytest.mark.skipif(not MARKITDOWN_AVAILABLE, reason="markitdown not installed")
def test_real_markitdown_smoke():
    # markitdown degrades gracefully: unparseable bytes come back as text
    # rather than raising. Document that contract with the real library.
    out = convert_to_markdown(b"%PDF-1.4 not a real pdf", "broken.pdf")
    assert isinstance(out, str) and out
