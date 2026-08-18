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
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
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
    "ConvertCancelled",
    "looks_office",
    "convert_to_markdown",
    "convert_to_markdown_subprocess",
]

# Formats routed through the converter. Conservative: only formats whose
# Markdown rendering is genuinely useful in a TUI. CSV/HTML/JSON/images are
# handled by earlier routes and deliberately excluded.
OFFICE_SUFFIXES = (".pdf", ".docx", ".pptx", ".xlsx", ".epub")


class ConvertError(Exception):
    """Any failure converting a document to Markdown (including a missing
    extra or empty output). Callers catch this one type and fall back."""


class ConvertCancelled(ConvertError):
    """The user cancelled the conversion; the child process was killed. A
    subclass of :class:`ConvertError` so existing ``except ConvertError``
    fallbacks keep working."""


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


def _package_root() -> str:
    """Directory that must be on ``PYTHONPATH`` for the child to import
    ``dunders`` when running from a source checkout (installed layouts already
    have it)."""
    return str(Path(__file__).resolve().parents[2])


def convert_to_markdown_subprocess(
    source: Path | bytes,
    name: str,
    *,
    cancel_event: "threading.Event | None" = None,
    on_tick: "Callable[[], None] | None" = None,
    poll: float = 0.15,
) -> str:
    """Same contract as :func:`convert_to_markdown`, but the conversion runs in
    a **child process**.

    markitdown (pdfminer et al.) is pure Python and CPU-bound: run in a thread
    it holds the GIL for the whole conversion, so the Textual event loop starves
    and the Converting… modal's Cancel button stops responding on a big PDF.
    A child process shares no GIL — the UI keeps painting — and can actually be
    killed, so Cancel is immediate instead of "wait for markitdown to finish".

    ``cancel_event`` is polled every ``poll`` seconds; when it is set the child
    is killed and :class:`ConvertCancelled` is raised. ``on_tick`` is called on
    each poll so the caller can animate an indeterminate progress bar.

    Falls back to the in-process conversion when the child cannot be spawned
    (frozen interpreter, restricted environment)."""
    if not MARKITDOWN_AVAILABLE:
        raise ConvertError("markitdown is not installed (pip install dunders[office])")
    if not sys.executable:
        return convert_to_markdown(source, name)

    tmp = Path(tempfile.mkdtemp(prefix="dunders-doc-"))
    src_path = Path(source) if not isinstance(source, (bytes, bytearray)) else None
    try:
        if src_path is None:
            # Keep the extension: the child routes by suffix, like markitdown.
            src_path = tmp / f"input{Path(name).suffix}"
            src_path.write_bytes(bytes(source))  # type: ignore[arg-type]
        out_path = tmp / "out.md"
        err_path = tmp / "err.txt"
        env = dict(os.environ)
        root = _package_root()
        env["PYTHONPATH"] = (
            root + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else root
        )
        cmd = [
            sys.executable,
            "-m",
            "dunders.fm.doc_converter",
            str(src_path),
            str(out_path),
        ]
        # stderr goes to a FILE, never a pipe: pdfminer is chatty and nobody
        # drains a pipe during the wait loop, which would deadlock the child.
        with err_path.open("wb") as err_file:
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=err_file,
                    env=env,
                )
            except OSError:
                return convert_to_markdown(source, name)
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    raise ConvertCancelled(f"conversion of {name} cancelled")
                if on_tick is not None:
                    on_tick()
                try:
                    proc.wait(timeout=poll)
                except subprocess.TimeoutExpired:
                    pass
        if proc.returncode != 0:
            detail = ""
            try:
                detail = err_path.read_text("utf-8", errors="replace").strip()
            except OSError:
                pass
            detail = detail.splitlines()[-1] if detail else f"exit {proc.returncode}"
            raise ConvertError(detail)
        try:
            text = out_path.read_text("utf-8")
        except OSError as exc:
            raise ConvertError(str(exc)) from exc
        if not text:
            raise ConvertError(f"empty conversion for {name}")
        return text
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _main(argv: list[str]) -> int:
    """Child-process entry point: ``python -m dunders.fm.doc_converter SRC DST``.
    Converts ``SRC`` and writes the Markdown to ``DST`` (UTF-8)."""
    if len(argv) != 2:
        print("usage: python -m dunders.fm.doc_converter SRC DST", file=sys.stderr)
        return 2
    src, dst = Path(argv[0]), Path(argv[1])
    try:
        text = convert_to_markdown(src, src.name)
    except ConvertError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    dst.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a child process
    raise SystemExit(_main(sys.argv[1:]))
