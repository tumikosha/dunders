"""App-level: Create archive (F-menu action_pack) and the F5 'zip:' overload."""

import zipfile
from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.fm.dialogs import CopyMoveDialog, PackDialog
from dunders.fm.file_panel import FilePanel
from dunders.windowing import Desktop, Window


def _seed(dirpath: Path) -> None:
    (dirpath / "a.txt").write_text("aaa")
    (dirpath / "dir").mkdir()
    (dirpath / "dir" / "inner.txt").write_text("inner")


def _panels(app: DundersApp):
    desktop = app.query_one(Desktop)
    left = desktop.query_one("#panel-left", Window).content
    right = desktop.query_one("#panel-right", Window).content
    assert isinstance(left, FilePanel) and isinstance(right, FilePanel)
    return left, right


def _cursor_on(panel: FilePanel, name: str) -> None:
    panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == name)


async def _settle(pilot):
    # pack/copy run on a worker thread; give it a few ticks to finish.
    for _ in range(20):
        await pilot.pause()


@pytest.mark.asyncio
async def test_action_pack_creates_archive_from_selection(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed(src)
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, _ = _panels(app)
        _cursor_on(left, "a.txt")
        app.action_pack()
        await pilot.pause()
        dialog = app.query_one(PackDialog)
        dialog._input.value = "bundle.zip"
        dialog.action_submit()
        await _settle(pilot)
        out = src / "bundle.zip"
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            assert zf.namelist() == ["a.txt"]
            assert zf.read("a.txt") == b"aaa"


@pytest.mark.asyncio
async def test_action_pack_refused_inside_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed(src)
    # Make an archive to browse into.
    with zipfile.ZipFile(src / "z.zip", "w") as zf:
        zf.writestr("m.txt", b"hi")
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, _ = _panels(app)
        _cursor_on(left, "z.zip")
        left.activate()
        await pilot.pause()
        assert left.cwd_loc.scheme == "zip"
        app.action_pack()  # must not raise, must not open a dialog
        await pilot.pause()
        assert not list(app.query(PackDialog))


@pytest.mark.asyncio
async def test_f5_zip_prefix_packs_into_archive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _seed(src)
    dst = tmp_path / "dst"
    dst.mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        _cursor_on(left, "dir")
        await pilot.press("f5")
        await pilot.pause()
        dialog = app.query_one(CopyMoveDialog)
        # Destination prefixed with "zip:" → pack instead of copy.
        dialog._input.value = "zip:packed.zip"
        dialog.action_submit()
        await _settle(pilot)
        out = dst / "packed.zip"
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            assert zf.read("dir/inner.txt") == b"inner"


def _seed_tree(src: Path) -> None:
    (src / "proj").mkdir()
    (src / "proj" / "main.py").write_text("print(1)")
    (src / "proj" / ".gitignore").write_text("*.pyc")
    (src / "proj" / ".git").mkdir()
    (src / "proj" / ".git" / "HEAD").write_text("ref")
    (src / "proj" / "venv").mkdir()
    (src / "proj" / "venv" / "lib.py").write_text("x")
    (src / "proj" / "myvenv2").mkdir()
    (src / "proj" / "myvenv2" / "lib.py").write_text("x")


@pytest.mark.asyncio
async def test_pack_dialog_format_level_and_filter(tmp_path):
    """Format swaps the suffix and hides the password row for 7z; the filter
    is off until ticked and prefilled with the default masks."""
    from dunders.fm.exclude import DEFAULT_PACK_EXCLUDE
    src = tmp_path / "src"
    src.mkdir()
    _seed_tree(src)
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, _ = _panels(app)
        _cursor_on(left, "proj")
        app.action_pack()
        await pilot.pause()
        dialog = app.query_one(PackDialog)
        assert dialog.get_value() == "proj.zip"
        assert dialog.fmt == "zip" and dialog.level == 5
        assert dialog._exclude_input.value == DEFAULT_PACK_EXCLUDE \
            == ".*;-.gitignore;venv*;myvenv*"
        assert dialog.exclude_masks == ()          # off until ticked
        dialog._format.focus()
        await pilot.press("right")                 # -> 7z
        await pilot.pause()
        assert dialog.fmt == "7z"
        assert dialog.get_value() == "proj.7z"
        assert dialog.query_one("#pk-password-row").has_class("-hidden")
        assert dialog.password is None
        await pilot.press("left")                  # back to zip
        await pilot.pause()
        assert dialog.get_value() == "proj.zip"
        dialog._level.focus()
        await pilot.press("right")                 # Normal(5) -> Maximum(7)
        await pilot.pause()
        assert dialog.level == 7
        dialog._exclude_on.focus()
        await pilot.press("space")
        await pilot.pause()
        assert dialog.exclude_masks == (".*", "-.gitignore", "venv*", "myvenv*")
        dialog.action_submit()
        await _settle(pilot)
        with zipfile.ZipFile(src / "proj.zip") as zf:
            assert sorted(zf.namelist()) == ["proj/.gitignore", "proj/main.py"]
            assert zf.getinfo("proj/main.py").compress_type == zipfile.ZIP_DEFLATED


def test_pack_zip_store_level_and_filter(tmp_path):
    from dunders.fm.actions import pack_paths
    _seed_tree(tmp_path)
    out = tmp_path / "o.zip"
    res = pack_paths([tmp_path / "proj"], out, base=tmp_path, level=0,
                     exclude=(".*", "-.gitignore", "venv*", "myvenv*"))
    assert not res.errors
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["proj/.gitignore", "proj/main.py"]
        assert zf.getinfo("proj/main.py").compress_type == zipfile.ZIP_STORED


_HAS_7Z = __import__("shutil").which("7z") or __import__("shutil").which("7za") \
    or __import__("shutil").which("7zz")


@pytest.mark.skipif(not _HAS_7Z, reason="no 7z binary")
def test_pack_7z_with_filter(tmp_path):
    import subprocess

    from dunders.fm.actions import pack_paths
    from dunders.fm.providers.sevenzip_provider import find_7z
    _seed_tree(tmp_path)
    out = tmp_path / "o.7z"
    seen = []
    res = pack_paths([tmp_path / "proj"], out, base=tmp_path, fmt="7z", level=9,
                     exclude=(".*", "-.gitignore", "venv*", "myvenv*"),
                     on_progress=lambda i, n: seen.append((i, n)))
    assert not res.errors and res.succeeded == [out]
    listing = subprocess.run([find_7z(), "l", "-ba", "-slt", str(out)],
                             capture_output=True, text=True).stdout
    paths = sorted(line[len("Path = "):] for line in listing.splitlines()
                   if line.startswith("Path = "))
    assert paths == ["proj/.gitignore", "proj/main.py"]
    assert seen[-1] == (100, 100)


@pytest.mark.skipif(not _HAS_7Z, reason="no 7z binary")
def test_pack_7z_cancel_removes_partial(tmp_path):
    import threading

    from dunders.fm.actions import pack_paths
    _seed_tree(tmp_path)
    ev = threading.Event()
    ev.set()
    out = tmp_path / "o.7z"
    res = pack_paths([tmp_path / "proj"], out, base=tmp_path, fmt="7z",
                     cancel_event=ev)
    assert res.cancelled and not out.exists()


def _big_tree(tmp_path: Path) -> int:
    import os
    (tmp_path / "proj").mkdir()
    data = os.urandom(1 << 20) + b"z" * (2 << 20)   # 3 MiB: several chunks
    (tmp_path / "proj" / "big.bin").write_bytes(data)
    (tmp_path / "proj" / "small.txt").write_text("hi")
    return len(data) + 2


@pytest.mark.parametrize("password", [None, "pw"])
def test_pack_zip_reports_bytes_for_speed_and_clock(tmp_path, password):
    """Packing feeds the dialog byte-level status: source bytes read drive
    the bars, the growing archive size is the "Packed" side."""
    from dunders.fm.actions import pack_paths
    total = _big_tree(tmp_path)
    out = tmp_path / "o.zip"
    seen = []
    res = pack_paths([tmp_path / "proj"], out, base=tmp_path, level=9,
                     password=password, on_status=seen.append)
    assert not res.errors
    last = seen[-1]
    assert last.compressed and last.is_bytes
    assert last.total == total and last.read_bytes == total
    assert last.written_bytes == out.stat().st_size
    assert last.files_done == last.files_total == 2
    # Streamed: the archive grew while big.bin was still being read.
    assert any(0 < s.written_bytes and s.read_bytes < total for s in seen)
    if password is None:
        with zipfile.ZipFile(out) as zf:
            assert zf.getinfo("proj/big.bin").compress_type == zipfile.ZIP_DEFLATED
            assert len(zf.read("proj/big.bin")) == total - 2


@pytest.mark.skipif(not _HAS_7Z, reason="no 7z binary")
def test_pack_7z_reports_bytes(tmp_path):
    from dunders.fm.actions import pack_paths
    total = _big_tree(tmp_path)
    out = tmp_path / "o.7z"
    seen = []
    res = pack_paths([tmp_path / "proj"], out, base=tmp_path, fmt="7z",
                     on_status=seen.append)
    assert not res.errors
    last = seen[-1]
    assert last.compressed and last.total == total
    assert last.read_bytes == total
    assert last.written_bytes == out.stat().st_size > 0
    assert last.file_total == 0          # no per-file progress from 7z


@pytest.mark.asyncio
async def test_progress_dialog_packing_layout():
    """Packing: no read-ahead tone, "Packed:" label, File bar hidden when
    there is no per-file progress (7z)."""
    from textual.app import App, ComposeResult

    from dunders.fm.actions import CopyStatus
    from dunders.fm.dialogs import ProgressDialog
    dlg = ProgressDialog(title="Packing", total=1)

    class _H(App):
        def compose(self) -> ComposeResult:
            yield dlg

    async with _H().run_test() as pilot:
        await pilot.pause()
        dlg.set_copy_status(CopyStatus(
            done=6 << 20, total=8 << 20, label="o.7z", is_bytes=True,
            read_bytes=6 << 20, written_bytes=1 << 20, compressed=True,
        ))
        await pilot.pause()
        bar = "".join(s.text for s in dlg.render_line(dlg._BAR_Y))
        assert dlg._BAR_READ not in bar
        file_row = "".join(s.text for s in dlg.render_line(dlg._FILE_BAR_Y))
        assert file_row.strip() == ""
        io2 = "".join(s.text for s in dlg.render_line(dlg._IO2_Y))
        assert "Packed:" in io2 and "1.0M" in io2
        io1 = "".join(s.text for s in dlg.render_line(dlg._IO_Y))
        assert "Elapsed:" in io1 and "/s" in io1
