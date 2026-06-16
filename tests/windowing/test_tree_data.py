"""JSON/YAML tree adapter + JsonYamlTreeContent."""

import json

from textual.app import App, ComposeResult

from dunders.windowing.core.tree_model import HeaderRow
from dunders.windowing.tree.data_adapter import (
    data_from_tree, parse_scalar, tree_from_data,
)
from dunders.windowing.tree.data_content import JsonYamlTreeContent


def test_adapter_round_trip():
    data = {
        "name": "demo", "port": 8080, "debug": True, "ratio": 1.5,
        "nothing": None, "tags": ["a", "b"], "db": {"host": "x", "pool": 4},
    }
    assert data_from_tree(tree_from_data(data)) == data
    assert data_from_tree(tree_from_data([1, "two", False])) == [1, "two", False]


def test_parse_scalar_infers_types():
    assert parse_scalar("5") == 5
    assert parse_scalar("3.14") == 3.14
    assert parse_scalar("true") is True
    assert parse_scalar("null") is None
    assert parse_scalar("hi") == "hi"
    assert parse_scalar('"quoted"') == "quoted"
    assert parse_scalar("2026-06-16") == "2026-06-16"


def test_editing_a_value_changes_its_type():
    root = tree_from_data({"port": "8080"})        # string in source
    node = root.children[0]
    node.body = "9090"
    assert data_from_tree(root) == {"port": 9090}  # inferred to int


class _App(App):
    def __init__(self, path):
        super().__init__()
        self._path = path
        self.content = None

    def compose(self) -> ComposeResult:
        self.content = JsonYamlTreeContent(self._path)
        yield self.content


async def test_json_content_loads_and_saves(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"name": "demo", "port": 8080, "hosts": ["a"]}))
    app = _App(str(p))
    async with app.run_test(size=(50, 18)) as pilot:
        c = app.content
        await pilot.pause()
        assert c.window_title == "config.json"
        labels = [r.node.label for r in c.widget.rows if isinstance(r, HeaderRow)]
        assert labels == ["name", "port", "hosts"]
        # edit the 'port' value and save -> reloaded as int
        port = next(n for n in c.root.children if n.label == "port")
        port.body = "9090"
        c.save()
        assert json.loads(p.read_text()) == {"name": "demo", "port": 9090, "hosts": ["a"]}
        assert c.is_dirty is False


async def test_yaml_content_round_trips(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("a: 1\nb:\n  - x\n  - 'y'\nflag: false\n")
    app = _App(str(p))
    async with app.run_test(size=(50, 18)) as pilot:
        c = app.content
        await pilot.pause()
        c.save()
        import yaml
        assert yaml.safe_load(p.read_text()) == {"a": 1, "b": ["x", "y"], "flag": False}


def test_jsonyaml_content_reexported():
    import dunders.windowing as W
    assert hasattr(W, "JsonYamlTreeContent")


async def test_jsonedit_app_smoke(tmp_path):
    from dunders.windowing.tree.jsonedit import JsonEditApp
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"k": "v", "n": 1}))
    app = JsonEditApp(str(p))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.pause()
        from dunders.windowing.tree.widget import TreeViewWidget
        tw = app.query_one(TreeViewWidget)
        labels = [r.node.label for r in tw.rows if isinstance(r, HeaderRow)]
        assert labels == ["k", "n"]


async def test_missing_file_opens_empty_and_creates_on_save(tmp_path):
    from dunders.windowing.core.tree_model import TreeNode
    p = tmp_path / "new.json"
    app = _App(str(p))
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        await pilot.pause()
        assert c.widget.rows == []            # empty doc, no crash
        assert not p.exists()
        c.root.add_child(TreeNode(label="k", body="1", data={"kind": "scalar"}))
        c.save()
        assert p.exists()
        assert json.loads(p.read_text()) == {"k": 1}


def test_main_reports_malformed_file(tmp_path, capsys):
    from dunders.windowing.tree.jsonedit import main
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json ]")
    assert main([str(bad)]) == 1
    assert "cannot parse" in capsys.readouterr().out


def test_main_usage_without_args(capsys):
    from dunders.windowing.tree.jsonedit import main
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out


async def test_action_bar_save_button(tmp_path):
    import json as _json
    from textual.geometry import Offset
    from dunders.windowing.tree.content import TreeActionBar
    p = tmp_path / "c.json"
    p.write_text('{"x": 1}')
    app = _App(str(p))
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.pause()
        bar = app.query_one(TreeActionBar)
        spans = bar._cells()
        assert any(s[2] == "save" for s in spans)
        next(n for n in c.root.children if n.label == "x").body = "42"
        sx = next(s[0] for s in spans if s[2] == "save")
        await pilot.click(bar, offset=Offset(sx, 0))      # ⇩ save
        await pilot.pause()
        await pilot.pause()
        assert _json.loads(p.read_text()) == {"x": 42}
        assert c.is_dirty is False


async def test_save_shows_toast(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"x": 1}')
    app = _App(str(p))
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        await pilot.pause()
        notes = []
        original = app.notify
        app.notify = lambda msg, **k: (notes.append(msg), original(msg, **k))[-1]
        c.save()
        await pilot.pause()
        assert any("Saved" in m and "c.json" in m for m in notes)


def test_interactively_added_children_are_saved():
    from dunders.windowing.core.tree_model import TreeNode
    # a node that started as a scalar leaf gets children -> becomes an object
    root = tree_from_data({"db": {"host": "x"}})
    host = root.children[0].children[0]            # scalar leaf, kind="scalar"
    host.add_child(TreeNode(label="sub", body="v"))
    assert data_from_tree(root) == {"db": {"host": {"sub": "v"}}}
    # a brand-new node (no kind metadata) with a child reconstructs as an object
    new = root.add_child(TreeNode(label="opts"))
    new.add_child(TreeNode(label="debug", body="true"))
    assert data_from_tree(root)["opts"] == {"debug": True}


def test_empty_containers_round_trip():
    data = {"empty_obj": {}, "empty_list": []}
    assert data_from_tree(tree_from_data(data)) == data
