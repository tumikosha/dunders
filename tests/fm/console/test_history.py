from __future__ import annotations

from pathlib import Path

from dunders.fm.console.history import History


def test_append_and_recall(tmp_path: Path):
    h = History(path=tmp_path / "h", cap=10)
    h.append("ls")
    h.append("cd /tmp")
    assert h.entries() == ["ls", "cd /tmp"]


def test_persisted_across_instances(tmp_path: Path):
    p = tmp_path / "h"
    h1 = History(path=p, cap=10)
    h1.append("a")
    h1.append("b")
    h2 = History(path=p, cap=10)
    assert h2.entries() == ["a", "b"]


def test_cap_keeps_most_recent(tmp_path: Path):
    h = History(path=tmp_path / "h", cap=3)
    for cmd in ["a", "b", "c", "d", "e"]:
        h.append(cmd)
    assert h.entries() == ["c", "d", "e"]


def test_blank_and_duplicate_consecutive_dropped(tmp_path: Path):
    h = History(path=tmp_path / "h", cap=10)
    h.append("ls")
    h.append("ls")  # consecutive dup -> dropped
    h.append("")    # blank -> dropped
    h.append("cd")
    assert h.entries() == ["ls", "cd"]


def test_navigation_cursor(tmp_path: Path):
    h = History(path=tmp_path / "h", cap=10)
    for c in ["a", "b", "c"]:
        h.append(c)
    h.reset_cursor()
    assert h.previous() == "c"
    assert h.previous() == "b"
    assert h.previous() == "a"
    assert h.previous() == "a"  # clamped at top
    assert h.next() == "b"
    assert h.next() == "c"
    assert h.next() == ""       # past end -> empty
