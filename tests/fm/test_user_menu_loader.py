from dunders.fm.user_menu_loader import (
    Row,
    build_rows,
    global_menu_path,
    load_menu,
    local_menu_path,
    seed_global_menu,
)

LOCAL = "## Local\n\n### (l) Local cmd\n```\necho local\n```\n"
GLOBAL = "## Global\n\n### (g) Global cmd\n```\necho global\n```\n"


def test_load_merges_local_then_separator_then_global(tmp_path):
    (tmp_path / ".dunders.menu.md").write_text(LOCAL, encoding="utf-8")
    global_menu_path().parent.mkdir(parents=True, exist_ok=True)
    global_menu_path().write_text(GLOBAL, encoding="utf-8")

    loaded = load_menu(tmp_path)
    kinds = [(r.kind, r.text or (r.entry.title if r.entry else "")) for r in loaded.rows]
    assert kinds == [
        ("header", "Local"),
        ("entry", "Local cmd"),
        ("separator", ""),
        ("header", "Global"),
        ("entry", "Global cmd"),
    ]
    assert loaded.has_any is True
    assert loaded.any_file_exists is True
    assert loaded.local_path == local_menu_path(tmp_path)


def test_load_global_only(tmp_path):
    global_menu_path().parent.mkdir(parents=True, exist_ok=True)
    global_menu_path().write_text(GLOBAL, encoding="utf-8")
    loaded = load_menu(tmp_path)
    assert [r.kind for r in loaded.rows] == ["header", "entry"]
    assert loaded.local_path is None


def test_load_no_files(tmp_path):
    loaded = load_menu(tmp_path)
    assert loaded.rows == []
    assert loaded.has_any is False
    assert loaded.any_file_exists is False


def test_seed_writes_once_and_is_parseable(tmp_path):
    from dunders.fm.user_menu import parse_menu

    path = seed_global_menu()
    assert path == global_menu_path()
    assert path.is_file()
    first = path.read_text(encoding="utf-8")
    entries = parse_menu(first)
    assert any("venv" in e.title.lower() for e in entries)
    # second call must not overwrite an existing file
    path.write_text("## Mine\n\n### (m) Mine\n```\necho mine\n```\n", encoding="utf-8")
    seed_global_menu()
    assert path.read_text(encoding="utf-8").startswith("## Mine")
