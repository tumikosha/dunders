"""Convert documents (PDF, office formats) to Markdown via markitdown.

The Markdown string is fed to the existing ``MarkdownViewerContent`` so an
F3 on a ``.pdf``/``.docx``/``.pptx``/``.xlsx``/``.epub`` opens rendered.

markitdown is an opt-in extra (``pip install dunders[office]``); it is MIT,
covers PDF and office formats through one converter, and is guarded by
``MARKITDOWN_AVAILABLE`` so the base package imports without it. ``looks_office``
is a pure name-only sniffer and imports nothing heavy, so it unit-tests in
isolation.
"""

from __future__ import annotations

import io
from pathlib import Path

try:  # markitdown is an opt-in extra (`pip install dunders[office]`).
    from markitdown import MarkItDown

    MARKITDOWN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    MarkItDown = None  # type: ignore[assignment, misc]
    MARKITDOWN_AVAILABLE = False

__all__ = [
    "OFFICE_SUFFIXES",
    "MARKITDOWN_AVAILABLE",
    "ConvertError",
    "looks_office",
    "convert_to_markdown",
]

# Formats routed through the converter. Conservative: only formats whose
# Markdown rendering is genuinely useful in a TUI. CSV/HTML/JSON/images are
# handled by earlier routes and deliberately excluded.
OFFICE_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".epub")


class ConvertError(Exception):
    """Any failure converting a document to Markdown (including a missing
    extra or empty output). Callers catch this one type and fall back."""


def looks_office(name: object) -> bool:
    """True if ``name`` has a document extension we convert. Cheap, name-only
    check; the caller's size guards still decide whether to attempt it."""
    return str(name).lower().endswith(OFFICE_SUFFIXES)


def convert_to_markdown(source: Path | bytes, name: str) -> str:
    """Convert ``source`` (a local path or in-memory bytes) to a Markdown
    string. ``name`` supplies the extension hint when ``source`` is bytes.

    Raises :class:`ConvertError` on a missing extra, a markitdown failure, or
    an empty conversion."""
    if not MARKITDOWN_AVAILABLE:
        raise ConvertError("markitdown is not installed (pip install dunders[office])")
    try:
        md = MarkItDown()
        if isinstance(source, (bytes, bytearray)):
            ext = Path(name).suffix
            result = md.convert_stream(io.BytesIO(bytes(source)), file_extension=ext)
        else:
            result = md.convert(str(source))
    except Exception as exc:  # markitdown raises a variety of types
        raise ConvertError(str(exc)) from exc
    text = getattr(result, "text_content", None) or getattr(result, "markdown", "")
    if not text:
        raise ConvertError(f"empty conversion for {name}")
    return text
