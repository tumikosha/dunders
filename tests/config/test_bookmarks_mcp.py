import json
from dunders.config.bookmarks import list_bookmarks, bookmarks_mtime


def _write(p, items):
    p.write_text(json.dumps({"bookmarks": items}), encoding="utf-8")


def test_list_bookmarks_reads_explicit_path(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///tmp"}])
    got = list_bookmarks(f)
    assert got == [{"label": "a", "uri": "file:///tmp"}]


def test_list_bookmarks_missing_path_is_empty(tmp_path):
    assert list_bookmarks(tmp_path / "nope.json") == []


def test_bookmarks_mtime_zero_when_missing(tmp_path):
    assert bookmarks_mtime(tmp_path / "nope.json") == 0.0


def test_bookmarks_mtime_changes_on_write(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///tmp"}])
    m1 = bookmarks_mtime(f)
    import os
    os.utime(f, (m1 + 10, m1 + 10))
    assert bookmarks_mtime(f) > m1
