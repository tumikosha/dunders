# tests/fm/test_associations_app.py
from pathlib import Path

from dunders.app import DundersApp
from dunders.fm.hex_viewer import HexViewerContent
from dunders.fm.image_viewer import ImageViewerContent
from dunders.fm.markdown_viewer import MarkdownViewerContent
from dunders.fm import associations_loader as L

# A tiny but valid JPEG header — starts with 0xff 0xd8 0xff, which is exactly
# the byte that crashed `path.read_text()`.
JPEG_BYTES = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


def _select(app, name):
    panel = app._active_panel()
    for i, e in enumerate(panel.entries):
        if e.name == name:
            panel.cursor = i
            return panel
    raise AssertionError(f"{name} not in panel: {[e.name for e in panel.entries]}")


async def test_f4_edit_on_jpg_does_not_crash_and_opens_hex(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(JPEG_BYTES)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        _select(app, "photo.jpg")
        app.action_edit()  # F4 — previously raised UnicodeDecodeError
        await _settle(pilot)
        # Undecodable file falls back to the hex viewer instead of crashing.
        assert list(app.query(HexViewerContent))


async def test_enter_on_jpg_opens_image_or_hex(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(JPEG_BYTES)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        panel = _select(app, "photo.jpg")
        entry = panel.entries[panel.cursor]
        app._dispatch_association(entry, "open")  # built-in default: image
        await _settle(pilot)
        assert list(app.query(ImageViewerContent)) or list(app.query(HexViewerContent))


async def test_large_md_opens_as_hex_not_markdown(tmp_path, monkeypatch):
    """Large/binary .md must short-circuit to the hex viewer (regression guard)."""
    md_file = tmp_path / "big.md"
    md_file.write_text("# Hello\n\nThis is a small but pretend-large markdown file.\n", encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # Monkeypatch the guard so it always reports the file as too large/binary.
        monkeypatch.setattr(app, "_should_use_hex_viewer", lambda p: True)
        panel = _select(app, "big.md")
        entry = panel.entries[panel.cursor]
        app._dispatch_association(entry, "open")  # built-in default for .md → markdown
        await _settle(pilot)
        assert list(app.query(HexViewerContent)), "Expected HexViewerContent for large .md"
        assert not list(app.query(MarkdownViewerContent)), "MarkdownViewerContent must NOT open for large .md"


async def test_external_command_runs_through_handover(tmp_path, monkeypatch):
    # User maps .foo edit to an external command; F4 must route to handover.
    L.associations_path().parent.mkdir(parents=True, exist_ok=True)
    L.associations_path().write_text(
        '[foo]\nedit = "!echo %f"\n', encoding="utf-8"
    )
    (tmp_path / "a.foo").write_text("hi", encoding="utf-8")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        calls = []
        monkeypatch.setattr(app, "_run_user_menu_body", lambda body, cwd: calls.append((body, cwd)))
        _select(app, "a.foo")
        app.action_edit()
        await _settle(pilot)
        assert calls and calls[0][0].startswith("echo ")
        assert "a.foo" in calls[0][0]
