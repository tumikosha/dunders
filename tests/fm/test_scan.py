import os
import stat
from pathlib import Path

from dunders.fm.scan import scan_dir


def test_scan_dir_returns_files_and_subdirs(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "sub").mkdir()
    entries = scan_dir(tmp_path, show_hidden=False, include_parent=False)
    by_name = {e.name: e for e in entries}
    assert set(by_name) == {"a.txt", "sub"}
    assert by_name["sub"].is_dir is True
    assert by_name["a.txt"].is_dir is False
    assert by_name["a.txt"].size == 2  # "hi"


def test_scan_dir_skips_hidden_by_default(tmp_path: Path):
    (tmp_path / "visible").write_text("")
    (tmp_path / ".hidden").write_text("")
    entries = scan_dir(tmp_path, show_hidden=False, include_parent=False)
    names = {e.name for e in entries}
    assert names == {"visible"}


def test_scan_dir_includes_hidden_when_requested(tmp_path: Path):
    (tmp_path / "visible").write_text("")
    (tmp_path / ".hidden").write_text("")
    entries = scan_dir(tmp_path, show_hidden=True, include_parent=False)
    names = {e.name for e in entries}
    assert names == {"visible", ".hidden"}


def test_scan_dir_prepends_parent_entry(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    entries = scan_dir(sub, show_hidden=False, include_parent=True)
    assert entries[0].name == ".."
    assert entries[0].is_dir is True
    assert entries[0].path == tmp_path


def test_scan_dir_omits_parent_at_root(tmp_path: Path):
    """At a filesystem root, parent.parent == self — don't add '..' there."""
    fake_root = tmp_path / "root"
    fake_root.mkdir()
    # Point cwd at fake_root and override parent to itself by passing
    # a path whose .parent is == itself: only true at "/" on POSIX. We
    # cannot reproduce that with tmp_path, so test the helper logic:
    entries = scan_dir(Path("/"), show_hidden=False, include_parent=True)
    assert all(e.name != ".." for e in entries[:1])  # first row is NOT ".."


def test_scan_dir_marks_symlink(tmp_path: Path):
    target = tmp_path / "target"
    target.write_text("hi")
    link = tmp_path / "link"
    link.symlink_to(target)
    entries = scan_dir(tmp_path, show_hidden=False, include_parent=False)
    by_name = {e.name: e for e in entries}
    assert by_name["link"].is_symlink is True
    assert by_name["target"].is_symlink is False


def test_scan_dir_marks_executable_files(tmp_path: Path):
    f = tmp_path / "run.sh"
    f.write_text("#!/bin/sh\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    entries = scan_dir(tmp_path, show_hidden=False, include_parent=False)
    e = next(x for x in entries if x.name == "run.sh")
    assert e.is_executable is True
    assert e.is_dir is False


def test_scan_dir_handles_unreadable_dir(tmp_path: Path, monkeypatch):
    """If os.scandir() raises, return parent-only (or empty) result, don't crash."""
    target = tmp_path / "locked"
    target.mkdir()

    def _raise(*_a, **_kw):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "scandir", _raise)
    entries = scan_dir(target, show_hidden=False, include_parent=True)
    # Parent entry is still reachable through stat() of target.parent
    # (which does not go through os.scandir). The body of the listing is empty.
    body = [e for e in entries if e.name != ".."]
    assert body == []


class _FakeDirEntry:
    """Minimal os.DirEntry stand-in; `vanish` makes stat() raise."""

    def __init__(self, real: Path, *, vanish: bool = False):
        self.name = real.name
        self.path = str(real)
        self._real = real
        self._vanish = vanish

    def stat(self, *, follow_symlinks: bool = True):
        if self._vanish:
            raise FileNotFoundError("vanished")
        return os.stat(self.path, follow_symlinks=follow_symlinks)

    def is_symlink(self):
        return self._real.is_symlink()

    def is_dir(self, *, follow_symlinks: bool = True):
        return self._real.is_dir()


class _FakeScandir:
    """Context-managed, iterable stand-in for os.scandir()'s return value."""

    def __init__(self, entries):
        self._entries = entries

    def __iter__(self):
        return iter(self._entries)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_scan_dir_skips_vanished_children(tmp_path: Path, monkeypatch):
    """A child whose stat() raises (e.g. deleted between readdir and stat)
    is silently skipped instead of crashing."""
    alive = tmp_path / "alive"
    alive.write_text("")
    dead = tmp_path / "dead"
    dead.write_text("")

    fake = _FakeScandir([
        _FakeDirEntry(alive),
        _FakeDirEntry(dead, vanish=True),
    ])
    monkeypatch.setattr(os, "scandir", lambda *_a, **_kw: fake)
    entries = scan_dir(tmp_path, show_hidden=False, include_parent=False)
    names = {e.name for e in entries}
    assert names == {"alive"}
