"""Pure-logic tests for dunders.fm.find_file.walk."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from dunders.fm.find_file import FindOptions, parse_masks, walk


def _opts(
    masks: tuple[str, ...] = ("*",),
    *,
    case_sensitive_mask: bool = False,
    contains: str = "",
    case_sensitive_text: bool = False,
    whole_words: bool = False,
    search_for_folders: bool = False,
    follow_symlinks: bool = False,
) -> FindOptions:
    return FindOptions(
        masks=masks,
        case_sensitive_mask=case_sensitive_mask,
        contains=contains,
        case_sensitive_text=case_sensitive_text,
        whole_words=whole_words,
        search_for_folders=search_for_folders,
        follow_symlinks=follow_symlinks,
    )


# --- parse_masks ----------------------------------------------------------

def test_parse_masks_empty():
    assert parse_masks("") == ()
    assert parse_masks("   ") == ()


def test_parse_masks_separators():
    # comma, semicolon, whitespace all accepted
    assert parse_masks("*.py,*.md") == ("*.py", "*.md")
    assert parse_masks("*.py; *.md") == ("*.py", "*.md")
    assert parse_masks("*.py *.md  *.txt") == ("*.py", "*.md", "*.txt")


# --- mask matching --------------------------------------------------------

def test_walk_matches_single_mask(tmp_path: Path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    res = walk(tmp_path, _opts(masks=("*.py",)))
    assert sorted(p.name for p in res.matches) == ["a.py"]
    assert res.cancelled is False


def test_walk_matches_multiple_masks(tmp_path: Path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.md").write_text("")
    (tmp_path / "c.txt").write_text("")
    res = walk(tmp_path, _opts(masks=("*.py", "*.md")))
    assert sorted(p.name for p in res.matches) == ["a.py", "b.md"]


def test_walk_case_insensitive_mask_default(tmp_path: Path):
    (tmp_path / "FOO.PY").write_text("")
    res = walk(tmp_path, _opts(masks=("*.py",), case_sensitive_mask=False))
    assert [p.name for p in res.matches] == ["FOO.PY"]


def test_walk_case_sensitive_mask(tmp_path: Path):
    (tmp_path / "FOO.PY").write_text("")
    (tmp_path / "bar.py").write_text("")
    res = walk(tmp_path, _opts(masks=("*.py",), case_sensitive_mask=True))
    assert [p.name for p in res.matches] == ["bar.py"]


def test_walk_descends_subdirs(tmp_path: Path):
    sub = tmp_path / "sub" / "deeper"
    sub.mkdir(parents=True)
    (sub / "x.py").write_text("")
    res = walk(tmp_path, _opts(masks=("*.py",)))
    assert [p.name for p in res.matches] == ["x.py"]


# --- contents-text --------------------------------------------------------

def test_walk_contains_text(tmp_path: Path):
    (tmp_path / "hit.txt").write_text("hello world")
    (tmp_path / "miss.txt").write_text("nothing here")
    res = walk(tmp_path, _opts(contains="hello"))
    assert [p.name for p in res.matches] == ["hit.txt"]


def test_walk_contains_case_insensitive(tmp_path: Path):
    (tmp_path / "hit.txt").write_text("Hello World")
    res = walk(tmp_path, _opts(contains="hello", case_sensitive_text=False))
    assert [p.name for p in res.matches] == ["hit.txt"]


def test_walk_contains_case_sensitive(tmp_path: Path):
    (tmp_path / "hit.txt").write_text("Hello World")
    res = walk(tmp_path, _opts(contains="hello", case_sensitive_text=True))
    assert res.matches == []


def test_walk_whole_words(tmp_path: Path):
    (tmp_path / "exact.txt").write_text("the cat sat")
    (tmp_path / "partial.txt").write_text("category lives here")
    res = walk(tmp_path, _opts(contains="cat", whole_words=True))
    assert [p.name for p in res.matches] == ["exact.txt"]


def test_walk_skips_binary_for_contains(tmp_path: Path):
    """Files with NUL in the first 8 KiB are treated as binary and skipped."""
    bin_file = tmp_path / "blob.bin"
    bin_file.write_bytes(b"hello\x00world")
    txt_file = tmp_path / "note.txt"
    txt_file.write_text("hello world")
    res = walk(tmp_path, _opts(contains="hello"))
    assert [p.name for p in res.matches] == ["note.txt"]


def test_walk_contains_across_chunk_boundary(tmp_path: Path):
    """Substrings spanning a 64 KiB read boundary still match."""
    target = tmp_path / "big.txt"
    chunk = 64 * 1024
    # Put "MARKER" exactly straddling a chunk boundary.
    needle = "MARKER"
    payload = ("a" * (chunk - 3)) + needle + ("b" * 100)
    target.write_text(payload)
    res = walk(tmp_path, _opts(contains=needle))
    assert [p.name for p in res.matches] == ["big.txt"]


# --- search_for_folders ---------------------------------------------------

def test_walk_finds_folder_when_enabled(tmp_path: Path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "x.txt").write_text("")
    res = walk(
        tmp_path,
        _opts(masks=("logs",), search_for_folders=True),
    )
    names = [p.name for p in res.matches]
    assert "logs" in names


def test_walk_skips_folder_when_disabled(tmp_path: Path):
    (tmp_path / "logs").mkdir()
    res = walk(
        tmp_path,
        _opts(masks=("logs",), search_for_folders=False),
    )
    assert res.matches == []


def test_walk_folder_with_contains_text_is_skipped(tmp_path: Path):
    """Folders match by name only — contains-text filters them out."""
    (tmp_path / "logs").mkdir()
    res = walk(
        tmp_path,
        _opts(
            masks=("logs",),
            search_for_folders=True,
            contains="anything",
        ),
    )
    assert res.matches == []


# --- cancel_event ---------------------------------------------------------

def test_walk_cancel_midway(tmp_path: Path):
    """Setting cancel_event during the walk stops further traversal."""
    # Build a wide tree so the walker has work to do.
    for i in range(20):
        sub = tmp_path / f"d{i:02d}"
        sub.mkdir()
        for j in range(10):
            (sub / f"f{j}.txt").write_text("")

    cancel = threading.Event()
    seen_dirs: list[Path] = []

    def on_progress(d: Path, files: int, folders: int) -> None:
        seen_dirs.append(d)
        if folders >= 3:
            cancel.set()

    res = walk(
        tmp_path,
        _opts(masks=("*.txt",)),
        on_progress=on_progress,
        cancel_event=cancel,
    )
    assert res.cancelled is True
    # Sanity: we stopped before scanning all 20 subdirs.
    assert res.folders_scanned < 21


# --- on_match streaming ---------------------------------------------------

def test_walk_on_match_called_per_hit(tmp_path: Path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    streamed: list[Path] = []
    res = walk(
        tmp_path,
        _opts(masks=("*.py",)),
        on_match=streamed.append,
    )
    assert sorted(p.name for p in streamed) == ["a.py", "b.py"]
    assert sorted(p.name for p in res.matches) == ["a.py", "b.py"]


# --- symlinks -------------------------------------------------------------

def test_walk_does_not_follow_symlink_dirs_by_default(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "x.py").write_text("")
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # platform without symlink support; skip silently
    res = walk(tmp_path, _opts(masks=("*.py",), follow_symlinks=False))
    # Should find x.py exactly once via the real path, not twice.
    assert len(res.matches) == 1
    assert res.matches[0].parent.name == "real"


def test_walk_symlink_cycle_protected(tmp_path: Path):
    """A symlink pointing back to an ancestor must not cause infinite loop."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "f.py").write_text("")
    cycle = a / "loop"
    try:
        # `loop` -> tmp_path (parent of `a`); descending into it would
        # otherwise re-enter `a` forever.
        os.symlink(tmp_path, cycle, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    res = walk(tmp_path, _opts(masks=("*.py",), follow_symlinks=True))
    # The walker terminates and finds f.py exactly once.
    assert len(res.matches) == 1
    assert res.matches[0].name == "f.py"
