"""GDriveProvider path/id mapping over a fake DriveApi (no network)."""

import pytest

from dunders.core.vfs import VfsPath
from dunders.fm.providers.gdrive.api import FOLDER_MIME, DriveApi
from dunders.fm.providers.gdrive_provider import GDriveProvider

from tests.fm.providers.test_gdrive_api import FakeDrive, FakeTransport


def _provider(drive):
    api = DriveApi(FakeTransport(drive), token_provider=lambda: "tok")
    return GDriveProvider(connector=lambda _root: api)


def _loc(*parts):
    return VfsPath(scheme="gdrive", root="me", parts=parts)


def _seed():
    d = FakeDrive()
    d.add("readme.txt", "root", content=b"hello")
    docs = d.add("docs", "root", mime=FOLDER_MIME)
    d.add("note.txt", docs, content=b"note")
    d.add("sub", docs, mime=FOLDER_MIME)
    return d


def test_scan_root_lists_children_with_parent():
    p = _provider(_seed())
    entries = p.scan(_loc())
    names = {e.name for e in entries}
    assert names == {"readme.txt", "docs"}          # root has no ".."
    by = {e.name: e for e in entries}
    assert by["docs"].is_dir and not by["readme.txt"].is_dir
    assert by["readme.txt"].size == 5


def test_scan_subfolder_has_parent_row():
    p = _provider(_seed())
    entries = p.scan(_loc("docs"))
    assert entries[0].name == ".." and entries[0].is_dir
    assert {e.name for e in entries[1:]} == {"note.txt", "sub"}


def test_is_dir():
    p = _provider(_seed())
    assert p.is_dir(_loc()) is True             # account root
    assert p.is_dir(_loc("docs")) is True
    assert p.is_dir(_loc("readme.txt")) is False
    assert p.is_dir(_loc("nope")) is False


def test_open_read_streams_content():
    p = _provider(_seed())
    with p.open_read(_loc("docs", "note.txt")) as fh:
        assert fh.read() == b"note"


def test_open_read_native_doc_exports():
    d = FakeDrive()
    d.add("Sheet1", "root", mime="application/vnd.google-apps.spreadsheet",
          export=b"XLSX")
    p = _provider(d)
    with p.open_read(_loc("Sheet1")) as fh:   # exported, not refused
        assert fh.read() == b"XLSX"


def test_exists():
    p = _provider(_seed())
    assert p.exists(_loc("readme.txt")) is True
    assert p.exists(_loc("docs", "note.txt")) is True
    assert p.exists(_loc("ghost.txt")) is False
    assert p.exists(_loc()) is True


def test_open_write_uploads_new_file():
    d = _seed()
    p = _provider(d)
    with p.open_write(_loc("new.txt")) as w:
        w.write(b"FRESH")
    assert any(f["name"] == "new.txt" and f["_content"] == b"FRESH"
               for f in d.files.values())


def test_open_write_no_clobber_without_overwrite():
    p = _provider(_seed())
    with pytest.raises(FileExistsError):
        p.open_write(_loc("readme.txt"))


def test_open_write_overwrite_replaces():
    d = _seed()
    p = _provider(d)
    with p.open_write(_loc("readme.txt"), overwrite=True) as w:
        w.write(b"REPLACED")
    matches = [f for f in d.files.values() if f["name"] == "readme.txt"]
    assert len(matches) == 1 and matches[0]["_content"] == b"REPLACED"


def test_mkdir():
    d = _seed()
    p = _provider(d)
    res = p.mkdir(_loc("docs"), "created")
    assert not res.errors
    docs_id = next(f["id"] for f in d.files.values() if f["name"] == "docs")
    assert any(f["name"] == "created" and f["mimeType"] == FOLDER_MIME
               and docs_id in f["parents"] for f in d.files.values())


def test_delete():
    d = _seed()
    p = _provider(d)
    res = p.delete([_loc("readme.txt")])
    assert not res.errors and res.succeeded == [_loc("readme.txt")]
    assert not any(f["name"] == "readme.txt" for f in d.files.values())


def test_copy_within_has_no_fast_path():
    p = _provider(_seed())
    assert p.copy_within([_loc("a")], _loc(), rename_to=None) is None
    assert p.move_within([_loc("a")], _loc()) is None


# --- cross-scheme copy through the generic transfer engine ------------------

def _registry(drive):
    from dunders.core.vfs import VfsRegistry
    from dunders.fm.vfs_local import LocalProvider
    api = DriveApi(FakeTransport(drive), token_provider=lambda: "tok")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    reg.register(GDriveProvider(connector=lambda _r: api))
    return reg, drive


def test_upload_local_to_gdrive_with_rename(tmp_path):
    from dunders.fm.vfs_engine import transfer
    reg, drive = _registry(_seed())
    src = tmp_path / "local.txt"
    src.write_text("UPLOAD")
    dest = _loc("docs")
    res = transfer(reg, [VfsPath.local(src)], dest, mode="copy",
                   rename_to="uploaded.txt")
    assert res.errors == []
    stored = next(f for f in drive.files.values() if f["name"] == "uploaded.txt")
    assert stored["_content"] == b"UPLOAD"


def test_download_gdrive_to_local(tmp_path):
    from dunders.fm.vfs_engine import transfer
    reg, _drive = _registry(_seed())
    out = tmp_path / "out"
    out.mkdir()
    res = transfer(reg, [_loc("docs", "note.txt")], VfsPath.local(out), mode="copy")
    assert res.errors == []
    assert (out / "note.txt").read_text() == "note"


def test_upload_overwrites_without_skip(tmp_path):
    from dunders.fm.vfs_engine import transfer
    reg, drive = _registry(_seed())
    src = tmp_path / "readme.txt"
    src.write_text("NEW")
    # no skip_existing -> replaces the existing Drive file (one copy remains)
    res = transfer(reg, [VfsPath.local(src)], _loc(), mode="copy")
    assert res.errors == []
    matches = [f for f in drive.files.values() if f["name"] == "readme.txt"]
    assert len(matches) == 1 and matches[0]["_content"] == b"NEW"


def test_export_as_file_for_native_doc():
    d = _seed()
    d.add("Report", "root", mime="application/vnd.google-apps.document",
          export=b"DOCX")
    p = _provider(d)
    result = p.export_as_file(_loc("Report"))
    assert result is not None
    name, reader = result
    assert name == "Report.docx"
    assert reader.read() == b"DOCX"


def test_export_as_file_none_for_regular_file():
    p = _provider(_seed())
    assert p.export_as_file(_loc("readme.txt")) is None   # normal binary -> None
    assert p.export_as_file(_loc("docs")) is None         # folder -> None


def test_copy_native_doc_exports_with_extension(tmp_path):
    from dunders.fm.vfs_engine import transfer
    d = _seed()
    d.add("Sheet1", "root", mime="application/vnd.google-apps.spreadsheet",
          export=b"XLSX-BYTES")
    reg, _ = _registry(d)
    out = tmp_path / "out"
    out.mkdir()
    res = transfer(reg, [_loc("Sheet1")], VfsPath.local(out), mode="copy")
    assert res.errors == []
    # exported to the Office extension, not a bare "Sheet1"
    assert (out / "Sheet1.xlsx").read_bytes() == b"XLSX-BYTES"
    assert not (out / "Sheet1").exists()


def test_copy_folder_with_native_doc(tmp_path):
    # A native doc NESTED in a copied folder is exported too (via open_read),
    # not skipped with an error.
    from dunders.fm.vfs_engine import transfer
    d = FakeDrive()
    folder = d.add("proj", "root", mime=FOLDER_MIME)
    d.add("a.txt", folder, content=b"plain")
    d.add("Notes", folder, mime="application/vnd.google-apps.document",
          export=b"DOCX")
    reg, _ = _registry(d)
    out = tmp_path / "out"
    out.mkdir()
    res = transfer(reg, [_loc("proj")], VfsPath.local(out), mode="copy")
    assert res.errors == []
    assert (out / "proj" / "a.txt").read_bytes() == b"plain"
    assert (out / "proj" / "Notes").read_bytes() == b"DOCX"   # exported content


def test_upload_skips_existing_with_flag(tmp_path):
    from dunders.fm.vfs_engine import transfer
    reg, drive = _registry(_seed())
    src = tmp_path / "readme.txt"
    src.write_text("NEW")
    res = transfer(reg, [VfsPath.local(src)], _loc(), mode="copy",
                   skip_existing=True)
    assert len(res.skipped) == 1
    kept = next(f for f in drive.files.values() if f["name"] == "readme.txt")
    assert kept["_content"] == b"hello"   # original untouched
