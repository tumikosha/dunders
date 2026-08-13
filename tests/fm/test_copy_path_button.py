import pytest
from textual.geometry import Offset

from dunders.app import DundersApp
from dunders.windowing.core import clipboard


def _panel_window(app, win_id):
    for w in app.desktop.windows:
        if str(w.id) == win_id:
            return w
    return None


@pytest.mark.asyncio
async def test_panels_have_copy_box(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        for wid in ("panel-left", "panel-right"):
            win = _panel_window(app, wid)
            assert win is not None
            assert win.decorations.copy_box is True
            assert win.decorations.close_box is True


@pytest.mark.asyncio
async def test_hit_test_copy_box_after_close(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        win = _panel_window(app, "panel-left")
        # close_box at x 1..3, copy_box at x 4..6 on the top edge.
        assert win.hit_test(Offset(2, 0)) == "close_box"
        assert win.hit_test(Offset(5, 0)) == "copy_box"


@pytest.mark.asyncio
async def test_copy_box_click_copies_cwd_and_notifies(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        copied = []
        notes = []
        monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))
        monkeypatch.setattr(app, "notify", lambda msg, **k: notes.append(msg))
        win = _panel_window(app, "panel-left")
        event = type("E", (), {"window": win})()
        app.on_window_copy_box_clicked(event)
        await pilot.pause()
        assert copied == [str(win.content.cwd)]
        assert any("copied" in n for n in notes)


# ---------------------------------------------------------------------------
# Editor title bar: [⧉D] directory, [⧉N] file name, [⧉P] full path
# ---------------------------------------------------------------------------

def _editor_window(app):
    from dunders.windowing.editor import EditorContent
    return next(w for w in app.desktop.windows if isinstance(w.content, EditorContent))


@pytest.mark.asyncio
async def test_editor_window_carries_the_three_copy_boxes(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[f])
    async with app.run_test() as pilot:
        await pilot.pause()
        deco = _editor_window(app).decorations
        assert (deco.copy_dir_box, deco.copy_name_box, deco.copy_path_box) == (
            True, True, True
        )


@pytest.mark.asyncio
async def test_an_untitled_buffer_has_no_copy_boxes():
    """Nothing to copy before the buffer has a path."""
    app = DundersApp(launch_mode="we", initial_paths=[])
    async with app.run_test() as pilot:
        await pilot.pause()
        deco = _editor_window(app).decorations
        assert not any(
            (deco.copy_dir_box, deco.copy_name_box, deco.copy_path_box)
        )


@pytest.mark.asyncio
async def test_hit_test_walks_the_three_boxes(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[f])
    async with app.run_test() as pilot:
        await pilot.pause()
        win = _editor_window(app)
        # [■][⧉D][⧉N][⧉P] — three cells for close, four for each copy button.
        assert win.hit_test(Offset(2, 0)) == "close_box"
        assert win.hit_test(Offset(4, 0)) == "copy_dir_box"
        assert win.hit_test(Offset(7, 0)) == "copy_dir_box"
        assert win.hit_test(Offset(8, 0)) == "copy_name_box"
        assert win.hit_test(Offset(11, 0)) == "copy_name_box"
        assert win.hit_test(Offset(12, 0)) == "copy_path_box"
        assert win.hit_test(Offset(15, 0)) == "copy_path_box"


@pytest.mark.asyncio
@pytest.mark.parametrize("part,expected", [
    ("dir", "parent"),
    ("name", "name"),
    ("path", "full"),
])
async def test_each_button_copies_its_own_part(tmp_path, monkeypatch, part, expected):
    sub = tmp_path / "src"
    sub.mkdir()
    f = sub / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[f])
    async with app.run_test() as pilot:
        await pilot.pause()
        copied = []
        monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))
        monkeypatch.setattr(app, "notify", lambda msg, **k: None)
        win = _editor_window(app)
        event = type("E", (), {"window": win, "part": part})()
        app.on_window_copy_part_requested(event)
        await pilot.pause()

        want = {"parent": str(sub), "name": "a.py", "full": str(f)}[expected]
        assert copied == [want]


# ---------------------------------------------------------------------------
# _copy_part — the split itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("part,expected", [
    ("dir", "/work/proj"),
    ("name", "CLAUDE.md"),
    ("path", "/work/proj/CLAUDE.md"),
])
def test_a_relative_buffer_path_is_absolutised(monkeypatch, part, expected):
    """`__ CLAUDE.md` stores the bare name — [D] gave "." and [P] gave the name."""
    from pathlib import Path as _Path

    from dunders.app import _copy_part

    monkeypatch.setattr(_Path, "cwd", classmethod(lambda cls: _Path("/work/proj")))
    assert _copy_part("CLAUDE.md", part) == expected


def test_dot_segments_are_normalised(monkeypatch):
    from pathlib import Path as _Path

    from dunders.app import _copy_part

    monkeypatch.setattr(_Path, "cwd", classmethod(lambda cls: _Path("/work/proj/src")))
    assert _copy_part("../docs/a.md", "path") == "/work/proj/docs/a.md"


@pytest.mark.parametrize("part,expected", [
    ("dir", "sftp://host/srv/etc"),
    ("name", "a.conf"),
    ("path", "sftp://host/srv/etc/a.conf"),
])
def test_a_vfs_locator_is_split_as_text_not_as_a_path(part, expected):
    """Path() would mangle a locator; it is not a filesystem path."""
    from dunders.app import _copy_part

    assert _copy_part("sftp://host/srv/etc/a.conf", part) == expected


def test_an_unknown_part_copies_nothing():
    from dunders.app import _copy_part

    assert _copy_part("/tmp/a.py", "everything") is None


@pytest.mark.asyncio
async def test_the_button_copies_an_absolute_path_for_a_relative_buffer(
    tmp_path, monkeypatch
):
    """End to end, the way `__ CLAUDE.md` actually opens a file."""
    (tmp_path / "CLAUDE.md").write_text("# doc\n")
    monkeypatch.chdir(tmp_path)
    app = DundersApp(launch_mode="we", initial_paths=["CLAUDE.md"])
    async with app.run_test() as pilot:
        await pilot.pause()
        copied = []
        monkeypatch.setattr(clipboard, "copy", lambda text, app=None: copied.append(text))
        monkeypatch.setattr(app, "notify", lambda msg, **k: None)
        win = _editor_window(app)
        for part in ("dir", "name", "path"):
            app.on_window_copy_part_requested(
                type("E", (), {"window": win, "part": part})()
            )
        await pilot.pause()

    cwd = str(tmp_path.resolve())
    assert copied == [cwd, "CLAUDE.md", f"{cwd}/CLAUDE.md"]


# ---------------------------------------------------------------------------
# Hover highlight — the frame buttons light up like status-bar ones
# ---------------------------------------------------------------------------

def _top_row_text(win):
    strip = win.render_line(0)
    return "".join(seg.text for seg in strip._segments)


def _reversed_text(win):
    strip = win.render_line(0)
    return "".join(
        seg.text for seg in strip._segments
        if seg.style is not None and seg.style.reverse
    )


@pytest.mark.asyncio
async def test_hovering_a_copy_button_highlights_exactly_it(tmp_path):
    from textual import events

    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[f])
    async with app.run_test() as pilot:
        await pilot.pause()
        win = _editor_window(app)
        assert _reversed_text(win) == ""          # nothing hot to begin with

        win._update_hover(Offset(9, 0))           # inside [⧉N]
        await pilot.pause()
        assert win._hover_target == "copy_name_box"
        assert _reversed_text(win) == "[⧉N]"

        win._update_hover(Offset(13, 0))          # moved on to [⧉P]
        await pilot.pause()
        assert _reversed_text(win) == "[⧉P]"

        win.on_leave(events.Leave(win))
        await pilot.pause()
        assert _reversed_text(win) == ""


@pytest.mark.asyncio
async def test_the_title_and_borders_do_not_highlight(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[f])
    async with app.run_test() as pilot:
        await pilot.pause()
        win = _editor_window(app)
        for x in (0, 20, win.size.width - 1):     # corner, title, corner
            win._update_hover(Offset(x, 0))
            await pilot.pause()
            assert _reversed_text(win) == "", f"column {x} lit up"


@pytest.mark.asyncio
async def test_highlighting_leaves_the_row_itself_intact(tmp_path):
    """Same characters, same width — only the styling differs."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    app = DundersApp(launch_mode="we", initial_paths=[f])
    async with app.run_test() as pilot:
        await pilot.pause()
        win = _editor_window(app)
        plain = _top_row_text(win)
        win._update_hover(Offset(5, 0))
        await pilot.pause()
        assert _top_row_text(win) == plain
