from dunders.fm.associations import (
    BUILTIN_DEFAULTS,
    BuiltinAction,
    ExternalAction,
    current_os_name,
    merge_tables,
    parse_associations,
    resolve,
)


def test_parse_string_and_per_os_table_and_bang():
    text = """
[jpg]
open = "image"

[jpg.edit]
default = "!xdg-open %f"
macos = "!open -a Preview %f"
"""
    table = parse_associations(text)
    assert table["jpg"]["open"] == "image"
    assert table["jpg"]["edit"] == {
        "default": "!xdg-open %f",
        "macos": "!open -a Preview %f",
    }


def test_parse_ignores_non_table_sections():
    # A top-level scalar is not an extension section; it must be dropped.
    assert parse_associations('bogus = 1\n[png]\nopen = "image"\n') == {
        "png": {"open": "image"}
    }


def test_resolve_builtin_handler():
    table = {"png": {"open": "image"}}
    assert resolve(table, "png", "open", "linux") == BuiltinAction("image")


def test_resolve_external_strips_bang_and_picks_os():
    table = {"jpg": {"edit": {"default": "!xdg-open %f", "macos": "!open %f"}}}
    assert resolve(table, "jpg", "edit", "macos") == ExternalAction("open %f")
    assert resolve(table, "jpg", "edit", "linux") == ExternalAction("xdg-open %f")


def test_resolve_missing_ext_or_verb_is_auto():
    assert resolve({}, "xyz", "open", "linux") == BuiltinAction("auto")
    assert resolve({"png": {"view": "image"}}, "png", "open", "linux") == BuiltinAction("auto")


def test_resolve_per_os_table_without_match_falls_back_to_auto():
    table = {"jpg": {"edit": {"macos": "!open %f"}}}
    assert resolve(table, "jpg", "edit", "linux") == BuiltinAction("auto")


def test_merge_overrides_at_verb_granularity():
    base = {"jpg": {"open": "image", "view": "image"}}
    user = {"jpg": {"edit": "!gimp %f"}}
    merged = merge_tables(base, user)
    assert merged["jpg"] == {"open": "image", "view": "image", "edit": "!gimp %f"}
    # base is not mutated
    assert "edit" not in base["jpg"]


def test_builtin_defaults_cover_jpg_open():
    assert BUILTIN_DEFAULTS["jpg"]["open"] == "image"


def test_current_os_name_is_one_of_known():
    assert current_os_name() in {"macos", "linux", "windows"}
