from dunders.fm.user_menu import MenuEntry, parse_menu

SAMPLE = """\
# User Menu

## Build & Test

### (b) Build project
```bash
make build
```

### (t) Run tests
```bash
pytest -q %d
```

## Git

### Status no hotkey
```sh
git status
```
"""


def test_parse_menu_extracts_entries_sections_hotkeys_bodies():
    entries = parse_menu(SAMPLE)
    assert [(e.hotkey, e.title, e.section) for e in entries] == [
        ("b", "Build project", "Build & Test"),
        ("t", "Run tests", "Build & Test"),
        (None, "Status no hotkey", "Git"),
    ]
    assert entries[0].body == "make build"
    assert entries[1].body == "pytest -q %d"


def test_parse_menu_skips_entry_without_code_block():
    text = "### (x) No body here\n\n### (y) Has body\n```\necho hi\n```\n"
    entries = parse_menu(text)
    assert [e.title for e in entries] == ["Has body"]
    assert entries[0].body == "echo hi"


def test_parse_menu_keeps_multiline_body():
    text = "### (m) Multi\n```bash\nset -e\nmake\nmake test\n```\n"
    entries = parse_menu(text)
    assert entries[0].body == "set -e\nmake\nmake test"


def test_parse_menu_empty_and_garbage():
    assert parse_menu("") == []
    assert parse_menu("just prose, no headings\n") == []


from dunders.fm.user_menu import MacroContext, collect_prompts, expand_macros


def _ctx(**kw):
    base = dict(current_file="a b.py", tagged=("a b.py", "c.txt"),
                panel_dir="/work dir", other_file="o.py", other_dir="/other")
    base.update(kw)
    return MacroContext(**base)


def test_expand_basic_macros_are_quoted():
    assert expand_macros("ls %d", _ctx(), {}) == "ls '/work dir'"
    assert expand_macros("cat %f", _ctx(), {}) == "cat 'a b.py'"
    assert expand_macros("e %F in %D", _ctx(), {}) == "e 'o.py' in '/other'"


def test_expand_tagged_and_selected():
    assert expand_macros("rm %t", _ctx(), {}) == "rm 'a b.py' 'c.txt'"
    assert expand_macros("rm %s", _ctx(), {}) == "rm 'a b.py' 'c.txt'"
    assert expand_macros("rm %s", _ctx(tagged=()), {}) == "rm 'a b.py'"


def test_expand_ext_basename_and_percent_literal():
    assert expand_macros("echo %x", _ctx(current_file="x.py"), {}) == "echo 'py'"
    assert expand_macros("echo %b", _ctx(current_file="x.py"), {}) == "echo 'x'"
    assert expand_macros("100%% done", _ctx(), {}) == "100% done"


def test_expand_absent_context_is_empty():
    c = _ctx(current_file=None, tagged=(), other_file=None, other_dir=None)
    assert expand_macros("a %f b %t c %F", c, {}) == "a  b  c "


def test_collect_and_expand_prompts():
    body = "deploy %{Target host} as %{User} on %{Target host}"
    assert collect_prompts(body) == ["Target host", "User"]
    out = expand_macros(body, _ctx(), {"Target host": "h1", "User": "root"})
    assert out == "deploy 'h1' as 'root' on 'h1'"
