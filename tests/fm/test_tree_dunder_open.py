"""The 'Tree (JSON/YAML)' dunder is registered and opens the cursor file."""

import json

from dunders.app import DundersApp
from dunders.windowing.tree import JsonYamlTreeContent


def _cursor_to(panel, name):
    for i, e in enumerate(panel.entries):
        if not e.is_dir and e.path.name == name:
            panel.cursor = i
            return True
    return False


async def test_tree_dunder_command_registered(tmp_path):
    (tmp_path / "c.json").write_text("{}")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        ids = {c.id for c in app.command_registry.all()}
        assert "dunder.open.tree" in ids


async def test_tree_dunder_opens_json_as_tree(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"name": "demo", "port": 8080}))
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert _cursor_to(panel, "config.json")
        app._open_tree_dunder()
        await pilot.pause()
        await pilot.pause()
        trees = [w for w in app.desktop.windows
                 if isinstance(w.content, JsonYamlTreeContent)]
        assert len(trees) == 1
        assert trees[0].content.window_title == "config.json"


async def test_tree_dunder_rejects_non_json(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel = app._active_panel()
        assert _cursor_to(panel, "notes.txt")
        app._open_tree_dunder()
        await pilot.pause()
        trees = [w for w in app.desktop.windows
                 if isinstance(w.content, JsonYamlTreeContent)]
        assert trees == []     # only .json/.yaml/.yml open as a tree


async def test_tree_dunder_in_underscore_menu(tmp_path):
    (tmp_path / "c.json").write_text("{}")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        menu = next(m for m in app._all_menus if getattr(m, "label", None) == "_")
        labels = [getattr(i, "label", None) for i in menu.items]
        assert "Tree (JSON/YAML)" in labels


async def test_file_menu_save_routes_to_tree(tmp_path):
    (tmp_path / "c.json").write_text('{"x": 1}')
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        _cursor_to(app._active_panel(), "c.json")
        app._open_tree_dunder()
        await pilot.pause()
        await pilot.pause()
        win = next(w for w in app.desktop.windows
                   if isinstance(w.content, JsonYamlTreeContent))
        next(n for n in win.content.root.children if n.label == "x").body = "99"
        # File -> Save dispatches command id "save" to the focused window
        assert app.dispatcher.dispatch("save") is True
        await pilot.pause()
        import json
        assert json.loads((tmp_path / "c.json").read_text()) == {"x": 99}
