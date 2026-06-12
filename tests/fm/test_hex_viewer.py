"""Smoke + behaviour tests for the F3 hex viewer.

The integration tests boot a real `DundersApp` to confirm that opening a binary
or oversized file routes F3 through `HexViewerContent` (mmap-backed) instead
of pre-loading the file via `read_text()`.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.hex_viewer import HexViewerContent, HexViewerWidget
from dunders.windowing import Desktop


@pytest.fixture
def big_binary_file() -> Iterator[Path]:
    # 6 MiB > _HEX_VIEW_SIZE_THRESHOLD (4 MiB), with a known marker for search.
    payload = b"A" * (3 * 1024 * 1024) + b"NEEDLE-HIT" + b"B" * (3 * 1024 * 1024)
    fd, path = tempfile.mkstemp(prefix="qwe_hex_", suffix=".bin")
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    yield Path(path)
    Path(path).unlink(missing_ok=True)


def test_widget_search_via_mmap_and_fallback(big_binary_file: Path) -> None:
    w = HexViewerWidget(big_binary_file)
    try:
        assert w.file_size == big_binary_file.stat().st_size
        assert w.mode == "hex"
        assert w._bytes_per_line() == 16

        pos_mmap = w._find_bytes(b"NEEDLE-HIT", 0)
        assert pos_mmap == 3 * 1024 * 1024

        # Force the chunked-read fallback path and confirm parity.
        if w._mm is not None:
            w._mm.close()
            w._mm = None
        pos_fallback = w._find_bytes(b"NEEDLE-HIT", 0)
        assert pos_fallback == 3 * 1024 * 1024
    finally:
        w.on_unmount()


def test_widget_mode_toggle_resizes_virtual_canvas(big_binary_file: Path) -> None:
    w = HexViewerWidget(big_binary_file)
    try:
        hex_lines = w._total_lines()
        w.set_mode("text")
        assert w.mode == "text"
        text_lines = w._total_lines()
        # text mode packs 80 bytes/line vs 16 → fewer lines.
        assert text_lines < hex_lines
    finally:
        w.on_unmount()


@pytest.mark.asyncio
async def test_f3_on_binary_file_opens_hex_viewer(
    tmp_path: Path, big_binary_file: Path
) -> None:
    # Plant the file on disk and use the same code path F3 uses.
    workdir = tmp_path
    target = workdir / "blob.bin"
    target.write_bytes(big_binary_file.read_bytes())

    app = DundersApp(launch_mode="fm", initial_path=str(workdir))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Sanity: the heuristic agrees this file goes to the hex viewer.
        assert app._should_use_hex_viewer(target) is True
        app._open_editor_window(target, read_only=True)
        await pilot.pause()

        desktop = app.query_one(Desktop)
        hex_windows = [
            w for w in desktop.windows if isinstance(w.content, HexViewerContent)
        ]
        assert len(hex_windows) == 1
        content: HexViewerContent = hex_windows[0].content
        assert content.widget.file_size == target.stat().st_size
        # Search routes through the widget and finds the marker.
        assert content.widget.search("NEEDLE-HIT") is True


@pytest.mark.asyncio
async def test_f3_on_small_text_file_uses_text_viewer(tmp_path: Path) -> None:
    """Heuristic: small ASCII file should NOT trigger the hex viewer."""
    target = tmp_path / "tiny.txt"
    target.write_text("hello world\n")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._should_use_hex_viewer(target) is False
