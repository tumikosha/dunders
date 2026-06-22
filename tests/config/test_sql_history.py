"""SQL console query history: per-connection CRUD over a 0600 sql_history.json."""

import json
import os
import stat

from dunders.config.sql_history import (
    clear,
    delete,
    load_history,
    record,
    sql_history_path,
)

ROOT = "sqlite:///t.db"


def test_empty_when_missing():
    assert load_history(ROOT) == []


def test_record_then_load_newest_first():
    assert record(ROOT, "select 1", ok=True, info="1 row(s)")
    assert record(ROOT, "select 2", ok=True, info="1 row(s)")
    hist = load_history(ROOT)
    assert [e["sql"] for e in hist] == ["select 2", "select 1"]
    assert hist[0]["ok"] is True
    assert hist[0]["info"] == "1 row(s)"
    assert isinstance(hist[0]["ts"], (int, float))


def test_failed_queries_are_recorded():
    record(ROOT, "selct 1", ok=False, info="Error: syntax")
    hist = load_history(ROOT)
    assert hist[0]["sql"] == "selct 1"
    assert hist[0]["ok"] is False


def test_whitespace_only_is_not_recorded():
    assert record(ROOT, "   \n\t ", ok=True, info="") is False
    assert load_history(ROOT) == []


def test_duplicate_moves_to_top_and_refreshes():
    record(ROOT, "select 1", ok=True, info="old")
    record(ROOT, "select 2", ok=True, info="x")
    # Re-running an identical query (same trimmed text) must not duplicate it;
    # it moves to the front with refreshed ok/info.
    record(ROOT, "  select 1  ", ok=False, info="new")
    hist = load_history(ROOT)
    assert [e["sql"] for e in hist] == ["  select 1  ", "select 2"]
    assert hist[0]["ok"] is False
    assert hist[0]["info"] == "new"


def test_cap_at_200_drops_oldest():
    for i in range(205):
        record(ROOT, f"select {i}", ok=True, info="")
    hist = load_history(ROOT)
    assert len(hist) == 200
    # Newest kept, oldest five dropped.
    assert hist[0]["sql"] == "select 204"
    assert hist[-1]["sql"] == "select 5"


def test_per_connection_isolation():
    record("sqlite:///a.db", "select 'a'", ok=True, info="")
    record("sqlite:///b.db", "select 'b'", ok=True, info="")
    assert [e["sql"] for e in load_history("sqlite:///a.db")] == ["select 'a'"]
    assert [e["sql"] for e in load_history("sqlite:///b.db")] == ["select 'b'"]


def test_delete_by_index():
    record(ROOT, "select 1", ok=True, info="")
    record(ROOT, "select 2", ok=True, info="")  # index 0 (newest)
    assert delete(ROOT, 0)
    assert [e["sql"] for e in load_history(ROOT)] == ["select 1"]
    assert delete(ROOT, 5) is False  # out of range


def test_clear():
    record(ROOT, "select 1", ok=True, info="")
    record(ROOT, "select 2", ok=True, info="")
    assert clear(ROOT)
    assert load_history(ROOT) == []


def test_file_is_0600():
    record(ROOT, "select 1", ok=True, info="")
    mode = stat.S_IMODE(os.stat(sql_history_path()).st_mode)
    assert mode == 0o600


def test_corrupt_file_reads_empty():
    sql_history_path().parent.mkdir(parents=True, exist_ok=True)
    sql_history_path().write_text("{ not json")
    assert load_history(ROOT) == []


def test_malformed_entries_filtered():
    sql_history_path().parent.mkdir(parents=True, exist_ok=True)
    sql_history_path().write_text(json.dumps({
        "connections": {ROOT: ["bad", {"sql": "ok", "ts": 1.0, "ok": True, "info": ""}]}
    }))
    assert [e["sql"] for e in load_history(ROOT)] == ["ok"]
