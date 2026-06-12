from pathlib import Path

import pytest

from dunders.app import DundersApp
from dunders.windowing import Desktop, Window
from dunders.windowing.editor import EditorContent


def test_we_mode_constructor_stores_paths(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("print('a')\n")
    b.write_text("print('b')\n")
    app = DundersApp(launch_mode="we", initial_paths=[str(a), b])
    assert app.launch_mode == "we"
    assert app.initial_paths == [Path(a), Path(b)]
    # Single-path field stays None in we-mode; no accidental crossover.
    assert app.initial_path is None


@pytest.mark.asyncio
async def test_make_editor_window_loads_file(tmp_path):
    f = tmp_path / "hello.py"
    f.write_text("print('hi')\n")
    app = DundersApp(launch_mode="we", initial_paths=[str(f)])
    async with app.run_test() as pilot:
        await pilot.pause()
        win = app._make_editor_window(
            f, position=(3, 2), size=(40, 10), win_id="editor-test"
        )
        assert isinstance(win, Window)
        assert win.id == "editor-test"
        assert isinstance(win.content, EditorContent)
        assert win.content._editor.buffer.file_path == str(f)


@pytest.mark.asyncio
async def test_make_editor_window_none_path_is_untitled(tmp_path):
    app = DundersApp(launch_mode="we", initial_paths=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        win = app._make_editor_window(
            None, position=(0, 0), size=(40, 10), win_id="editor-untitled"
        )
        assert isinstance(win.content, EditorContent)
        assert win.content._editor.buffer.file_path is None


def _editor_windows(app):
    desktop = app.query_one(Desktop)
    return [w for w in desktop.windows if w.id and w.id.startswith("editor-")]


@pytest.mark.asyncio
async def test_we_three_files_cascade(tmp_path):
    files = []
    for name in ("a.py", "b.py", "c.py"):
        p = tmp_path / name
        p.write_text(f"# {name}\n")
        files.append(p)
    app = DundersApp(launch_mode="we", initial_paths=[str(p) for p in files])
    async with app.run_test() as pilot:
        await pilot.pause()
        desktop = app.query_one(Desktop)
        eds = _editor_windows(app)
        assert len(eds) == 3
        hidden_ids = {w.id for w in desktop.hidden_windows}
        assert {"panel-left", "panel-right"} <= hidden_ids
        top = desktop.windows[-1]
        assert top is desktop.focused_window
        assert top.content._editor.buffer.file_path == str(files[0])
        by_path = {w.content._editor.buffer.file_path: w for w in eds}

        def offset_xy(w):
            off = w.styles.offset
            return (int(off.x.value), int(off.y.value))

        assert offset_xy(by_path[str(files[0])]) == (0, 0)
        assert offset_xy(by_path[str(files[1])]) == (2, 1)
        assert offset_xy(by_path[str(files[2])]) == (4, 2)
        W = desktop.usable_size.width
        H = desktop.usable_size.height
        last = by_path[str(files[2])]
        last_off = last.styles.offset
        assert int(last_off.x.value) + last.size.width == W
        assert int(last_off.y.value) + last.size.height == H


@pytest.mark.asyncio
async def test_we_missing_file_opens_empty_buffer(tmp_path):
    missing = tmp_path / "nope.py"
    app = DundersApp(launch_mode="we", initial_paths=[str(missing)])
    async with app.run_test() as pilot:
        await pilot.pause()
        eds = _editor_windows(app)
        assert len(eds) == 1
        assert eds[0].content._editor.buffer.file_path == str(missing)
        assert eds[0].content._editor.buffer.lines == [""]


@pytest.mark.asyncio
async def test_we_no_args_opens_one_untitled(tmp_path):
    app = DundersApp(launch_mode="we", initial_paths=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        eds = _editor_windows(app)
        assert len(eds) == 1
        assert eds[0].content._editor.buffer.file_path is None


@pytest.mark.asyncio
async def test_we_directory_arg_is_skipped(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    f = tmp_path / "real.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[str(d), str(f)])
    async with app.run_test() as pilot:
        await pilot.pause()
        eds = _editor_windows(app)
        assert len(eds) == 1
        assert eds[0].content._editor.buffer.file_path == str(f)
