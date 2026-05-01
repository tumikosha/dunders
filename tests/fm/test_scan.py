import os
import stat
from pathlib import Path

import pytest

from tyui.fm.scan import scan_dir


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
    """If iterdir() raises, return parent-only (or empty) result, don't crash."""
    target = tmp_path / "locked"
    target.mkdir()

    def _raise(*_a, **_kw):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", _raise)
    entries = scan_dir(target, show_hidden=False, include_parent=True)
    # Parent entry is still reachable through stat() of target.parent.
    # The body of the listing is empty.
    body = [e for e in entries if e.name != ".."]
    assert body == []


def test_scan_dir_skips_vanished_children(tmp_path: Path, monkeypatch):
    """A child whose lstat() raises (e.g., it was deleted between iterdir
    and lstat) is silently skipped instead of crashing."""
    (tmp_path / "alive").write_text("")
    (tmp_path / "dead").write_text("")

    real_lstat = Path.lstat

    def _maybe_raise(self):
        if self.name == "dead":
            raise FileNotFoundError("vanished")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", _maybe_raise)
    entries = scan_dir(tmp_path, show_hidden=False, include_parent=False)
    names = {e.name for e in entries}
    assert names == {"alive"}
