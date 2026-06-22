"""transfer() — the VFS copy/move dispatcher.

Intra-provider transfers must behave exactly like the old copy_paths /
move_paths; cross-provider transfers are an explicit not-yet boundary.
"""

import zipfile

from dunders.core.vfs import VfsPath
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry


def _reg():
    return default_registry()


class TestIntraProvider:
    def test_copy(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest), mode="copy"
        )
        assert not res.errors
        assert (dest / "a.txt").read_text() == "payload"
        assert src.exists()  # copy keeps source

    def test_move(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest), mode="move"
        )
        assert not res.errors
        assert (dest / "a.txt").read_text() == "payload"
        assert not src.exists()  # move removes source

    def test_copy_with_rename(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        res = transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest),
            mode="copy", rename_to="renamed.txt",
        )
        assert not res.errors
        assert (dest / "renamed.txt").read_text() == "payload"

    def test_progress_called(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("x")
        dest = tmp_path / "dest"
        dest.mkdir()
        seen: list[tuple[int, int]] = []
        transfer(
            _reg(), [VfsPath.local(src)], VfsPath.local(dest),
            mode="copy", on_progress=lambda i, n: seen.append((i, n)),
        )
        assert seen  # provider forwarded the progress callback


class TestBoundaries:
    def test_empty_sources_is_noop(self, tmp_path):
        res = transfer(_reg(), [], VfsPath.local(tmp_path), mode="copy")
        assert not res.errors and not res.succeeded


def _make_zip(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("top.txt", b"hello")
        zf.writestr("dir/inner.txt", b"world")
        zf.writestr("dir/sub/deep.txt", b"deep")
    return path


class TestCrossProviderExtraction:
    def test_extract_single_member_zip_to_local(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("top.txt",))
        res = transfer(_reg(), [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "top.txt").read_bytes() == b"hello"
        assert res.succeeded == [dest / "top.txt"]

    def test_extract_directory_recursively(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("dir",))
        res = transfer(_reg(), [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "dir" / "inner.txt").read_bytes() == b"world"
        assert (dest / "dir" / "sub" / "deep.txt").read_bytes() == b"deep"

    def test_extract_with_rename(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("top.txt",))
        transfer(
            _reg(), [src], VfsPath.local(dest), mode="copy", rename_to="renamed.txt"
        )
        assert (dest / "renamed.txt").read_bytes() == b"hello"

    def test_progress_reported(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("dir",))
        seen: list[tuple[int, int]] = []
        transfer(
            _reg(), [src], VfsPath.local(dest), mode="copy",
            on_progress=lambda i, n: seen.append((i, n)),
        )
        assert seen[-1] == (2, 2)  # dir/ has two files (inner.txt, sub/deep.txt)

    def test_move_out_of_zip_extracts_and_removes_member(self, tmp_path):
        archive = _make_zip(tmp_path / "a.zip")
        dest = tmp_path / "out"
        dest.mkdir()
        src = VfsPath(scheme="zip", root=str(archive), parts=("top.txt",))
        res = transfer(_reg(), [src], VfsPath.local(dest), mode="move")
        # zip is writable now, so move truly moves: extracted out AND removed.
        assert not res.errors
        assert (dest / "top.txt").read_bytes() == b"hello"
        with zipfile.ZipFile(archive) as zf:
            assert "top.txt" not in zf.namelist()
            assert "dir/inner.txt" in zf.namelist()  # rest intact


from dunders.fm.providers.db_provider import DbProvider


def test_table_copies_out_as_single_jsonl(tmp_path):
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url); c.insert("users", {"name": "Ann"}); c.close()

    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))
    dest = VfsPath.local(str(tmp_path))

    res = transfer(reg, [root.child("users")], dest, mode="copy")
    assert not res.errors
    assert (tmp_path / "users.jsonl").exists()
    assert "Ann" in (tmp_path / "users.jsonl").read_text()


def test_table_export_keeps_jsonl_suffix_when_renamed(tmp_path):
    """The copy dialog pre-fills the source dir-name (a table has no extension),
    so rename_to drops the suffix; the export must re-add ``.jsonl``."""
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url); c.insert("users", {"name": "Ann"}); c.close()

    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))
    dest = VfsPath.local(str(tmp_path))

    # rename_to="users" mirrors `str(dest / targets[0].name)` from the dialog.
    res = transfer(reg, [root.child("users")], dest, mode="copy", rename_to="users")
    assert not res.errors
    assert (tmp_path / "users.jsonl").exists()
    assert not (tmp_path / "users").exists()


class _FlipEvent:
    """Cancel signal that fires only *after* the first export chunk is written.

    ``is_set()`` returns ``False`` for the outer-loop guard and the first
    in-loop ``_cancelled`` check, then ``True`` on the second iteration — so the
    writer is already open with a partial ``.jsonl`` on disk when ``_Cancelled``
    is raised, exercising the export branch's ``_cleanup_partial`` path.
    """

    def __init__(self, fire_after: int = 2) -> None:
        self._calls = 0
        self._fire_after = fire_after

    def is_set(self) -> bool:
        self._calls += 1
        return self._calls > self._fire_after


def test_table_export_cancel_cleans_partial(tmp_path):
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url)
    # Big enough that the JSONL export exceeds the 1 MiB stream chunk, so the
    # write loop runs more than once and a real partial file lands on disk
    # before the cancel fires.
    blob = "x" * 2000
    for i in range(900):
        c.insert("users", {"name": f"{blob}{i}"})
    c.close()

    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))
    dest = VfsPath.local(str(tmp_path))

    res = transfer(
        reg, [root.child("users")], dest, mode="copy",
        cancel_event=_FlipEvent(fire_after=2),
    )
    assert res.cancelled
    # the partial destination must be cleaned up, not left truncated on disk
    assert not (tmp_path / "users.jsonl").exists()


# -- Carry-over I1: table move is copy-only (source table survives) ----------

def test_table_move_to_file_dir_is_copy_only(tmp_path):
    """Moving a DB table out produces the .jsonl but does NOT delete the source
    table — export_as_file hits the ``continue`` in the engine so the
    move-delete tail is never reached (data-safe by design)."""
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url); c.insert("users", {"name": "Ann"}); c.close()

    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))
    dest = VfsPath.local(str(tmp_path))

    res = transfer(reg, [root.child("users")], dest, mode="move")
    assert not res.errors
    # The .jsonl was produced
    assert (tmp_path / "users.jsonl").exists()
    # The source table still exists (export branch skips the move-delete tail)
    conn = p.conn_for(root.root)
    assert "users" in conn.tables()
    assert conn.count("users") > 0


# -- Carry-over I2: delete on a non-record locator is a clean no-op ----------

def test_copy_local_jsonl_into_db_with_rename_names_table(tmp_path):
    """Copying a local .jsonl into a db root with rename_to imports into the
    named table (the path the copy dialog uses so a user can set/rename the
    target table instead of inheriting the source filename)."""
    from dunders.fm.providers.db_provider import DbProvider
    src = tmp_path / "pages2.jsonl"
    src.write_bytes(b'{"name": "A"}\n{"name": "B"}\n')
    url = f"sqlite:///{tmp_path/'t.db'}"
    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    db_root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))

    res = transfer(reg, [VfsPath.local(str(src))], db_root,
                   mode="copy", rename_to="germany.jsonl")
    assert not res.errors
    conn = p.conn_for(db_root.root)
    assert "germany" in conn.tables()       # named by rename_to, not "pages2"
    assert "pages2" not in conn.tables()
    assert conn.count("germany") == 2


def test_delete_table_loc_drops_table(tmp_path):
    """delete() handed a real table locator drops the table; a non-table
    single-part loc (e.g. an index name) stays a clean no-op."""
    from dunders.fm.providers import db_access as da
    url = f"sqlite:///{tmp_path/'t.db'}"
    c = da.DbConn.open(url); c.insert("users", {"name": "Ann"}); c.close()

    reg = default_registry()
    p: DbProvider = reg.for_scheme("db")
    root = p.resolve_target(url, base=VfsPath.local(str(tmp_path)))

    idx_loc = root.child("some_index")       # parts=("some_index",) — not a table
    result = p.delete([idx_loc])
    assert not result.errors
    assert "users" in p.conn_for(root.root).tables()  # non-table loc untouched

    table_loc = root.child("users")          # parts=("users",) — a real table
    result = p.delete([table_loc])
    assert not result.errors
    assert "users" not in p.conn_for(root.root).tables()  # dropped
