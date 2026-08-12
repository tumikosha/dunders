import dunders.main as m


def test_flags_parse():
    args = m._parse_args(["--mcp", "--mcp-write",
                          "--mcp-mounts", "a,b", "--mcp-bookmarks", "/tmp/bm.json"])
    assert args.mcp and args.mcp_write
    assert args.mcp_mounts == "a,b" and args.mcp_bookmarks == "/tmp/bm.json"


def test_main_mcp_branch_runs_server_not_tui(monkeypatch):
    called = {}
    monkeypatch.setattr(m, "_run_mcp", lambda args: called.setdefault("ran", True))
    # DundersApp must never be constructed in --mcp mode
    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("TUI started in --mcp mode")
    monkeypatch.setattr(m, "DundersApp", Boom)
    monkeypatch.setattr(m.sys, "argv", ["dunders", "--mcp"])
    m.main()
    assert called.get("ran") is True


def test_run_mcp_builds_table_and_serves(monkeypatch, tmp_path):
    import json
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": []}), encoding="utf-8")
    seen = {}

    def fake_run_stdio(registry, table, *, allow_write):
        seen["allow_write"] = allow_write
        seen["labels"] = {mt.label for mt in table.mounts()}
    monkeypatch.setattr(m, "run_stdio", fake_run_stdio)
    args = m._parse_args(["--mcp", "--mcp-bookmarks", str(f)])
    m._run_mcp(args)
    assert seen["allow_write"] is False and seen["labels"] == set()
