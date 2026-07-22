"""scan_folder_stats — recursive directory statistics walker."""

import threading
from pathlib import Path

from dunders.core.vfs import VfsPath
from dunders.fm.folder_stats import FolderStats, scan_folder_stats
from dunders.fm.vfs_local import default_registry


def _reg():
    return default_registry()


def _tree(root: Path) -> Path:
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "a.txt").write_text("AAAA")           # 4 bytes
    (root / ".hidden").write_text("HH")            # 2 bytes, hidden
    (root / "sub" / "b.bin").write_bytes(b"X" * 1000)  # largest
    (root / "sub" / "deep" / "c.txt").write_text("C")  # 1 byte
    return root


def test_counts_files_dirs_bytes(tmp_path: Path):
    root = _tree(tmp_path / "root")
    st = scan_folder_stats(_reg(), VfsPath.local(root))
    assert st.files == 4          # a.txt, .hidden, b.bin, c.txt
    assert st.dirs == 2           # sub, deep
    assert st.total_bytes == 4 + 2 + 1000 + 1
    assert st.partial is False


def test_hidden_files_are_counted(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "visible").write_text("xx")
    (root / ".secret").write_text("yyyy")
    st = scan_folder_stats(_reg(), VfsPath.local(root))
    assert st.files == 2
    assert st.total_bytes == 2 + 4


def test_largest_file_and_depth(tmp_path: Path):
    root = _tree(tmp_path / "root")
    st = scan_folder_stats(_reg(), VfsPath.local(root))
    assert st.largest_name == "b.bin"
    assert st.largest_size == 1000
    assert st.max_depth == 2  # root/sub/deep


def test_empty_dir(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    st = scan_folder_stats(_reg(), VfsPath.local(root))
    assert st == FolderStats()  # all zeros, not partial


def test_cancel_before_start_yields_partial(tmp_path: Path):
    root = _tree(tmp_path / "root")
    ev = threading.Event()
    ev.set()
    st = scan_folder_stats(_reg(), VfsPath.local(root), cancel_event=ev)
    assert st.partial is True
    assert st.files == 0


def test_on_progress_receives_snapshots(tmp_path: Path):
    # Many small files so the throttle (every 256 entries) fires at least once
    # mid-walk, plus the guaranteed final emit.
    root = tmp_path / "many"
    root.mkdir()
    for i in range(600):
        (root / f"f{i}.txt").write_text("x")
    seen: list[FolderStats] = []
    st = scan_folder_stats(_reg(), VfsPath.local(root), on_progress=seen.append)
    assert st.files == 600
    assert len(seen) >= 2                     # at least one throttled + final
    assert seen[-1].files == 600              # final snapshot is complete
    assert all(isinstance(s, FolderStats) for s in seen)
    # Snapshots are independent copies, not the same mutating object.
    assert seen[0] is not st
