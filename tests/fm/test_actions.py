import os
import threading
from pathlib import Path

import pytest

from dunders.fm.actions import (
    CopyStatus,
    OpError,
    OpResult,
    copy_paths,
    delete_paths,
    mkdir_at,
    move_paths,
)


# ---------------------------------------------------------------- mkdir

def test_mkdir_creates_directory(tmp_path: Path):
    result = mkdir_at(tmp_path, "newdir")
    assert result.errors == []
    assert result.succeeded == [tmp_path / "newdir"]
    assert (tmp_path / "newdir").is_dir()


def test_mkdir_fails_if_exists(tmp_path: Path):
    (tmp_path / "newdir").mkdir()
    result = mkdir_at(tmp_path, "newdir")
    assert result.succeeded == []
    assert len(result.errors) == 1
    assert result.errors[0].path == tmp_path / "newdir"


def test_mkdir_creates_nested_path(tmp_path: Path):
    """mkdir_at with a/b/c creates the chain."""
    result = mkdir_at(tmp_path, "a/b/c")
    assert result.errors == []
    assert (tmp_path / "a" / "b" / "c").is_dir()


# ---------------------------------------------------------------- copy

def test_copy_file_to_dir(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("hi")
    dest = tmp_path / "dest"
    dest.mkdir()
    result = copy_paths([src], dest)
    assert result.errors == []
    assert (dest / "a.txt").read_text() == "hi"
    assert src.exists()  # copy preserves source


def test_copy_directory_recursive(tmp_path: Path):
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f.txt").write_text("x")
    (src / "sub").mkdir()
    (src / "sub" / "g.txt").write_text("y")
    dest = tmp_path / "dest"
    dest.mkdir()
    result = copy_paths([src], dest)
    assert result.errors == []
    assert (dest / "tree" / "f.txt").read_text() == "x"
    assert (dest / "tree" / "sub" / "g.txt").read_text() == "y"


def test_copy_skips_when_dest_equals_source_dir(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("hi")
    result = copy_paths([src], tmp_path)
    # Copying file to its own parent dir is a no-op error (would overwrite self).
    assert result.succeeded == []
    assert len(result.errors) == 1


def test_copy_records_per_path_errors(tmp_path: Path):
    good = tmp_path / "good.txt"
    good.write_text("g")
    bad = tmp_path / "missing.txt"  # never created
    dest = tmp_path / "dest"
    dest.mkdir()
    result = copy_paths([good, bad], dest)
    assert {p.name for p in result.succeeded} == {"good.txt"}
    assert {e.path.name for e in result.errors} == {"missing.txt"}


def test_copy_cancellation(tmp_path: Path):
    a = tmp_path / "a.txt"
    a.write_text("a")
    b = tmp_path / "b.txt"
    b.write_text("b")
    dest = tmp_path / "dest"
    dest.mkdir()
    cancel = threading.Event()
    cancel.set()  # cancel before any work
    result = copy_paths([a, b], dest, cancel_event=cancel)
    assert result.cancelled is True
    assert result.succeeded == []


# ---------------------------------------------------------------- move

def test_move_renames_within_same_filesystem(tmp_path: Path):
    src = tmp_path / "a.txt"
    src.write_text("hi")
    dest = tmp_path / "dest"
    dest.mkdir()
    result = move_paths([src], dest)
    assert result.errors == []
    assert (dest / "a.txt").read_text() == "hi"
    assert not src.exists()


def test_move_directory(tmp_path: Path):
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f").write_text("x")
    dest = tmp_path / "dest"
    dest.mkdir()
    result = move_paths([src], dest)
    assert result.errors == []
    assert (dest / "tree" / "f").read_text() == "x"
    assert not src.exists()


# ---------------------------------------------------------------- delete

def test_delete_file(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    result = delete_paths([f])
    assert result.errors == []
    assert not f.exists()


def test_delete_directory_recursive(tmp_path: Path):
    d = tmp_path / "tree"
    d.mkdir()
    (d / "f").write_text("x")
    result = delete_paths([d])
    assert result.errors == []
    assert not d.exists()


def test_delete_records_per_path_errors(tmp_path: Path):
    good = tmp_path / "good"
    good.write_text("")
    bad = tmp_path / "missing"  # never created
    result = delete_paths([good, bad])
    assert {p.name for p in result.succeeded} == {"good"}
    assert {e.path.name for e in result.errors} == {"missing"}


def test_delete_progress_callback(tmp_path: Path):
    a = tmp_path / "a"
    a.write_text("")
    b = tmp_path / "b"
    b.write_text("")
    seen: list[tuple[int, int]] = []
    delete_paths([a, b], on_progress=lambda i, n: seen.append((i, n)))
    # Reports start at (0, total), then bumps after each processed entry.
    # Two top-level files -> total=2, three callback calls.
    assert seen == [(0, 2), (1, 2), (2, 2)]


def test_delete_progress_counts_files_inside_directory(tmp_path: Path):
    """Directory with N files should report N+1 progress steps (files + dir)."""
    d = tmp_path / "tree"
    d.mkdir()
    (d / "f1").write_text("")
    (d / "f2").write_text("")
    (d / "f3").write_text("")
    seen: list[tuple[int, int]] = []
    delete_paths([d], on_progress=lambda i, n: seen.append((i, n)))
    # 3 files + 1 dir = 4 entries. Bar starts at 0 and ends at total.
    assert seen[0] == (0, 4)
    assert seen[-1] == (4, 4)


def test_copy_progress_counts_files_inside_directory(tmp_path: Path):
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f1").write_text("")
    (src / "f2").write_text("")
    dst = tmp_path / "dst"
    dst.mkdir()
    seen: list[tuple[int, int]] = []
    copy_paths([src], dst, on_progress=lambda i, n: seen.append((i, n)))
    # The counter is file-based now ("file X of Y"): 2 files, dirs not counted.
    assert seen[0] == (0, 2)
    assert seen[-1] == (2, 2)


def test_copy_status_reports_bytes_and_filename(tmp_path: Path):
    """on_status moves the bar by bytes and names the current file."""
    src = tmp_path / "big.bin"
    payload = b"x" * (1024 * 1024 * 3 + 7)  # 3 chunks + a tail
    src.write_bytes(payload)
    dest = tmp_path / "dest"
    dest.mkdir()
    seen: list[CopyStatus] = []
    result = copy_paths([src], dest, on_status=seen.append)
    assert result.errors == []
    assert (dest / "big.bin").read_bytes() == payload
    # Byte mode, the final update lands at the full size, label is the source.
    assert all(s.is_bytes for s in seen)
    assert seen[0].done == 0
    assert seen[-1].done == len(payload)
    assert seen[-1].total == len(payload)
    assert any(s.label.endswith("big.bin") for s in seen)
    # The bar genuinely animates within the single file (multiple updates).
    assert len({s.done for s in seen}) > 2


def test_copy_status_carries_two_bar_fields(tmp_path: Path):
    """CopyStatus reports the current file's own progress + a file counter so
    the dialog can show a second bar."""
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("aaaa")          # 4
    (src / "sub" / "b.txt").write_bytes(b"x" * 100)
    dest = tmp_path / "dest"
    dest.mkdir()
    seen: list[CopyStatus] = []
    copy_paths([src], dest, rename_to="tree", on_status=seen.append)
    assert seen
    last = seen[-1]
    assert last.files_total == 2                 # a.txt + b.txt (dirs not counted)
    assert last.files_done == 2
    # While copying the 100-byte file its per-file total is reported.
    assert any(s.file_total == 100 for s in seen)
    # The per-file counter resets between files (starts each file at 0).
    assert any(s.file_done == 0 for s in seen)


def test_copy_paths_skip_existing(tmp_path: Path):
    """skip_existing leaves already-present destination files untouched and
    records them in result.skipped, while still copying the new ones."""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("NEW-A")
    (src / "b.txt").write_text("NEW-B")
    (src / "sub" / "c.txt").write_text("NEW-C")
    dest = tmp_path / "dst"
    (dest / "src").mkdir(parents=True)
    (dest / "src" / "a.txt").write_text("OLD-A")   # pre-existing -> skipped

    result = copy_paths([src], dest, rename_to="src", skip_existing=True)
    assert result.errors == []
    assert {Path(p).name for p in result.skipped} == {"a.txt"}
    assert (dest / "src" / "a.txt").read_text() == "OLD-A"   # untouched
    assert (dest / "src" / "b.txt").read_text() == "NEW-B"   # copied
    assert (dest / "src" / "sub" / "c.txt").read_text() == "NEW-C"


def test_copy_paths_measure_is_cancellable(tmp_path: Path):
    """Cancelling during the pre-copy sizing pass aborts immediately (the tree
    used to be walked twice with no cancel check — a frozen, dead-Cancel wait)."""
    import threading
    src = tmp_path / "big"
    src.mkdir()
    for i in range(20):
        (src / f"f{i}").write_text("x")
    dest = tmp_path / "dst"
    dest.mkdir()
    ev = threading.Event()
    ev.set()  # cancel before we even start measuring
    result = copy_paths([src], dest, cancel_event=ev)
    assert result.cancelled is True
    assert not (dest / "big").exists()  # nothing copied


def test_copy_paths_emits_measuring_status(tmp_path: Path):
    """A copy reports a 'Measuring…' status while sizing so the dialog shows
    life instead of a frozen 0% bar."""
    src = tmp_path / "many"
    src.mkdir()
    for i in range(5000):
        (src / f"f{i}").write_text("x")
    dest = tmp_path / "dst"
    dest.mkdir()
    seen: list[CopyStatus] = []
    copy_paths([src], dest, rename_to="many", on_status=seen.append)
    assert any(s.label == "Measuring…" for s in seen)
    # After measuring, the real copy still reports the full file count.
    assert seen[-1].files_total == 5000
    assert seen[-1].files_done == 5000


def test_copy_paths_no_skip_overwrites(tmp_path: Path):
    """Without skip_existing an existing file is overwritten (default)."""
    src = tmp_path / "f.txt"
    src.write_text("NEW")
    dest = tmp_path / "dst"
    dest.mkdir()
    (dest / "f.txt").write_text("OLD")
    copy_paths([src], dest)
    assert (dest / "f.txt").read_text() == "NEW"


def test_copy_status_suppresses_legacy_on_progress(tmp_path: Path):
    """When on_status is wired up, the legacy counter stays quiet (no clash)."""
    src = tmp_path / "a.txt"
    src.write_text("hi")
    dest = tmp_path / "dest"
    dest.mkdir()
    progress_calls: list[tuple[int, int]] = []
    status_calls: list[CopyStatus] = []
    copy_paths(
        [src], dest,
        on_progress=lambda i, n: progress_calls.append((i, n)),
        on_status=status_calls.append,
    )
    assert progress_calls == []
    assert status_calls


def test_copy_cancel_mid_file_removes_partial(tmp_path: Path):
    """Cancel during a big single file stops mid-stream and unlinks the partial."""
    src = tmp_path / "big.bin"
    src.write_bytes(b"y" * (1024 * 1024 * 5))
    dest = tmp_path / "dest"
    dest.mkdir()
    cancel = threading.Event()

    def _on_status(status: CopyStatus) -> None:
        if status.done >= 1024 * 1024:  # let one chunk through, then cancel
            cancel.set()

    result = copy_paths([src], dest, on_status=_on_status, cancel_event=cancel)
    assert result.cancelled is True
    assert not (dest / "big.bin").exists()  # partial cleaned up


def test_copy_cancellation_mid_tree(tmp_path: Path):
    """Cancelling during a deep copy stops at the next file boundary."""
    src = tmp_path / "big"
    src.mkdir()
    for i in range(50):
        (src / f"f{i}").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    cancel = threading.Event()
    progressed = [False]

    def _on_progress(i: int, n: int) -> None:
        if i > 5 and not progressed[0]:
            progressed[0] = True
            cancel.set()

    result = copy_paths([src], dst, on_progress=_on_progress, cancel_event=cancel)
    assert result.cancelled is True
    # Some but not all files should have been copied before the cancel
    # was honoured at the next file-boundary check.
    copied = list((dst / "big").iterdir()) if (dst / "big").exists() else []
    assert 0 < len(copied) < 50
