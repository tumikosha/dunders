# tests/fm/test_associations_loader.py
from dunders.fm import associations_loader as L
from dunders.fm.associations import BuiltinAction, resolve


def test_load_table_without_file_returns_defaults():
    table, err = L.load_table()
    assert err is None
    # jpg defaults present even with no user file.
    assert resolve(table, "jpg", "open", "linux") == BuiltinAction("image")


def test_user_file_overrides_at_verb_granularity():
    L.associations_path().parent.mkdir(parents=True, exist_ok=True)
    L.associations_path().write_text('[jpg]\nopen = "hex"\n', encoding="utf-8")
    table, err = L.load_table()
    assert err is None
    assert resolve(table, "jpg", "open", "linux") == BuiltinAction("hex")
    # view still comes from defaults
    assert resolve(table, "jpg", "view", "linux") == BuiltinAction("image")


def test_broken_toml_falls_back_to_defaults_with_error():
    L.associations_path().parent.mkdir(parents=True, exist_ok=True)
    L.associations_path().write_text("this is = = not toml", encoding="utf-8")
    table, err = L.load_table()
    assert err is not None
    assert resolve(table, "jpg", "open", "linux") == BuiltinAction("image")


def test_seed_writes_once_and_is_valid_toml():
    path = L.seed_associations()
    assert path.is_file()
    before = path.read_text(encoding="utf-8")
    # A second seed must not overwrite.
    path.write_text(before + "\n# edited\n", encoding="utf-8")
    L.seed_associations()
    assert "# edited" in path.read_text(encoding="utf-8")
    # The seed parses and loads cleanly.
    _, err = L.load_table()
    assert err is None
