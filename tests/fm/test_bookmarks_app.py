"""App-level bookmark flows: add (Ctrl+D) and open/remove (Ctrl+B)."""

import pytest

from dunders.app import DundersApp
from dunders.config.bookmarks import add_bookmark, list_bookmarks
from dunders.core.vfs import VfsPath
from dunders.fm.dialogs import AddBookmarkDialog


@pytest.mark.asyncio
async def test_ctrl_d_adds_current_local_location(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_add_bookmark()
        await pilot.pause()
        dialog = app.query_one(AddBookmarkDialog)
        # network checkbox absent for a local location
        assert dialog._ask_password is False
        dialog._label_input.value = "my place"
        dialog.action_submit()
        await pilot.pause()
        items = list_bookmarks()
        assert len(items) == 1
        assert items[0]["label"] == "my place"
        assert items[0]["uri"] == VfsPath.local(tmp_path).as_uri()
        assert items[0]["password"] is None


@pytest.mark.asyncio
async def test_enter_in_label_field_submits(tmp_path):
    # Regression: Enter while the label Input is focused must submit (the Input
    # posts Input.Submitted, which the dialog's on_input_submitted routes to
    # action_submit). Previously there was no handler and submit was impossible.
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_add_bookmark()
        await pilot.pause()
        dialog = app.query_one(AddBookmarkDialog)
        assert dialog._label_input.has_focus  # focused on open
        dialog._label_input.value = "via enter"
        await pilot.press("enter")
        await pilot.pause()
        items = list_bookmarks()
        assert [b["label"] for b in items] == ["via enter"]


@pytest.mark.asyncio
async def test_ctrl_b_opens_local_bookmark(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "marker.txt").write_text("x")
    add_bookmark("t", VfsPath.local(target).as_uri(), None)
    start = tmp_path / "start"
    start.mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(start))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app._active_panel()
        app.action_open_bookmarks()
        await pilot.pause()
        from dunders.fm.dialogs import BookmarksDialog
        dialog = app.query_one(BookmarksDialog)
        dialog._open_index(0)  # select the first bookmark
        await pilot.pause()
        assert panel.cwd_loc == VfsPath.local(target)
        assert any(e.name == "marker.txt" for e in panel.entries)


@pytest.mark.asyncio
async def test_picker_remove(tmp_path):
    add_bookmark("a", "file:///a", None)
    add_bookmark("b", "file:///b", None)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_open_bookmarks()
        await pilot.pause()
        from dunders.fm.dialogs import BookmarksDialog
        dialog = app.query_one(BookmarksDialog)
        dialog._remove_index(0)
        await pilot.pause()
        assert [b["label"] for b in list_bookmarks()] == ["b"]


@pytest.mark.asyncio
async def test_picker_cell_click_opens_and_removes(tmp_path):
    # The table routes a click on the ✗ (column 0) cell to remove, and a click
    # on the Label/Path cells to open. Removing keeps the picker open and
    # refreshes the rows in place.
    add_bookmark("a", "file:///a", None)
    add_bookmark("b", "file:///b", None)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_open_bookmarks()
        await pilot.pause()
        from dunders.fm.dialogs import BookmarksDialog

        dialog = app.query_one(BookmarksDialog)
        # A click on the ✗ (column 0) of row 0 ("a") deletes it; the dialog
        # stays open and "b" remains.
        dialog._on_cell_click(0, dialog._DEL_COL)
        await pilot.pause()
        assert [b["label"] for b in list_bookmarks()] == ["b"]
        assert app.query(BookmarksDialog)  # still open
        assert dialog._table.row_count == 1


@pytest.mark.asyncio
async def test_add_bookmark_for_db_loc_offers_password_checkbox(tmp_path):
    # A db:// location is a "slow" provider, so the Add-bookmark dialog must
    # offer the "remember password" checkbox. Regression: the Ctrl+B → Add
    # current path lost the db scheme (fell back to panel-left), hiding it.
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_add_bookmark(loc=VfsPath(scheme="db", root="sqlite:////x.db"))
        await pilot.pause()
        dialog = app.query_one(AddBookmarkDialog)
        assert dialog._ask_password is True


@pytest.mark.asyncio
async def test_add_bookmark_db_with_password_defaults_remember_on(tmp_path):
    # When the live db connection has a password, "remember password" is
    # pre-checked so a user who just authenticated doesn't silently save a
    # password-less bookmark (which fails on reopen with "no password supplied").
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        prov = app._vfs_registry.for_scheme("db")
        prov._urls["postgresql://u@h:5432/db"] = "postgresql://u:secret@h:5432/db"
        app.action_add_bookmark(loc=VfsPath(scheme="db", root="postgresql://u@h:5432/db"))
        await pilot.pause()
        dialog = app.query_one(AddBookmarkDialog)
        assert dialog._ask_password is True
        assert dialog._remember.checked is True      # pre-checked: password present
        assert dialog._label_input.value == "db"     # db name, not the raw URL


@pytest.mark.asyncio
async def test_add_bookmark_db_dialog_does_not_clip_checkbox_or_buttons(tmp_path):
    # Regression: the modal must be tall enough that the "remember password"
    # row AND the Save/Cancel buttons fit inside the window — they were clipped
    # off the bottom (window only 7 rows), so the checkbox "didn't appear".
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_add_bookmark(loc=VfsPath(scheme="db", root="postgresql://u@h/db"))
        await pilot.pause()
        await pilot.pause()
        dlg = app.query_one(AddBookmarkDialog)
        dr = dlg.region
        for sel in ("#ab-remember", "#ab-save", "#ab-cancel"):
            w = dlg.query_one(sel)
            assert w.display and w.region.height > 0
            assert w.region.y >= dr.y, f"{sel} above dialog"
            assert w.region.y + w.region.height <= dr.y + dr.height, f"{sel} clipped off the bottom"


@pytest.mark.asyncio
async def test_db_bookmark_reopens_with_clean_url(tmp_path, monkeypatch):
    # Regression: a db:// bookmark's root is a full SQLAlchemy URL; reopening
    # must pass it verbatim (with the saved password) — not append a "/" that
    # would corrupt the database name (mydb -> mydb/).
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        captured = {}
        monkeypatch.setattr(
            app, "_do_open_dunder",
            lambda scheme, spec, **kw: captured.update(
                scheme=scheme, spec=spec, password=kw.get("password")),
        )
        app._open_bookmark({
            "label": "pg",
            "uri": "db://postgresql://user@host:5432/mydb",
            "password": "secret",
        })
        assert captured == {
            "scheme": "db",
            "spec": "postgresql://user@host:5432/mydb",
            "password": "secret",
        }


@pytest.mark.asyncio
async def test_ctrl_b_shows_password_checkbox_for_db_panel(tmp_path):
    # The Bookmarks picker (Ctrl+B) shows an inline "remember password" checkbox
    # when the active location is a password-capable connection (db://).
    from dunders.fm.dialogs import BookmarksDialog, _PermCheckbox
    from dunders.windowing import Window
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        prov = app._vfs_registry.for_scheme("db")
        prov._urls["postgresql://u@h/db"] = "postgresql://u:secret@h/db"
        prov._conns["postgresql://u@h/db"] = object()
        panel = app.desktop.query_one("#panel-left", Window).content
        panel.cwd_loc = VfsPath(scheme="db", root="postgresql://u@h/db")
        app._focus_panel("panel-left")
        await pilot.pause()
        app.action_open_bookmarks()
        await pilot.pause()
        dialog = app.query_one(BookmarksDialog)
        cb = dialog.query_one("#bm-remember", _PermCheckbox)
        assert cb.checked is True  # pre-checked because the connection has a password


@pytest.mark.asyncio
async def test_ctrl_b_no_password_checkbox_for_local_panel(tmp_path):
    # A local panel has nothing to remember → no checkbox.
    from dunders.fm.dialogs import BookmarksDialog, _PermCheckbox
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        app.action_open_bookmarks()
        await pilot.pause()
        dialog = app.query_one(BookmarksDialog)
        assert len(dialog.query(_PermCheckbox)) == 0


@pytest.mark.asyncio
async def test_bookmarks_listed_in_brand_menu(tmp_path):
    add_bookmark("srv one", "file:///srv1", None)
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        brand = next(m for m in app.menu_bar.menus if m.label == "_")
        labels = [getattr(it, "label", None) for it in brand.items]
        assert "srv one" in labels
        assert any(lbl and "Add current" in lbl for lbl in labels)
