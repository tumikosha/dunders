"""DriveApi over an in-memory fake transport — no network."""

import io
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from dunders.fm.providers.gdrive.api import (
    FOLDER_MIME,
    DriveApi,
    DriveError,
    DriveFile,
)

_META_KEYS = ("id", "name", "mimeType", "size", "modifiedTime", "trashed")


class FakeDrive:
    """A tiny in-memory Drive: id -> metadata + content."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self._n = 0

    def add(self, name, parent, *, mime="text/plain", content=b"",
            export=b"", modified="2026-01-01T00:00:00Z"):
        self._n += 1
        fid = f"id{self._n}"
        self.files[fid] = {
            "id": fid, "name": name, "mimeType": mime,
            "size": str(len(content)), "modifiedTime": modified,
            "trashed": False, "parents": [parent],
            "_content": content, "_export": export,
        }
        return fid

    def meta(self, fid):
        return {k: self.files[fid][k] for k in _META_KEYS}


class FakeTransport:
    """Interprets the URLs DriveApi builds against a FakeDrive."""

    def __init__(self, drive: FakeDrive):
        self.drive = drive
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, *, headers, body=None):
        self.calls.append((method, url))
        assert headers.get("Authorization", "").startswith("Bearer ")
        u = urlparse(url)
        qs = parse_qs(u.query)
        fid_m = re.search(r"/files/([^/?]+)", u.path)

        if method == "GET" and u.path.endswith("/files"):
            return self._list(qs)
        if method == "GET" and fid_m and "alt" not in qs:
            fid = fid_m.group(1)
            if fid not in self.drive.files:
                return 404, b'{"error":{"message":"not found"}}'
            return 200, json.dumps(self.drive.meta(fid)).encode()
        if method == "POST" and "/upload/" in u.path:
            return self._upload(body)
        if method == "POST" and u.path.endswith("/files"):
            meta = json.loads(body)
            fid = self.drive.add(meta["name"], meta["parents"][0],
                                 mime=meta.get("mimeType", "text/plain"))
            return 200, json.dumps(self.drive.meta(fid)).encode()
        if method == "PATCH" and fid_m:
            self.drive.files[fid_m.group(1)]["name"] = json.loads(body)["name"]
            return 200, json.dumps(self.drive.meta(fid_m.group(1))).encode()
        if method == "DELETE" and fid_m:
            self.drive.files.pop(fid_m.group(1), None)
            return 204, b""
        return 404, b'{"error":{"message":"unhandled"}}'

    def open(self, method, url, *, headers):
        self.calls.append((method, url))
        path = urlparse(url).path
        fid = re.search(r"/files/([^/?]+)", path).group(1)
        if fid not in self.drive.files:
            return 404, io.BytesIO(b'{"error":{"message":"gone"}}')
        key = "_export" if path.endswith("/export") else "_content"
        return 200, io.BytesIO(self.drive.files[fid][key])

    def _list(self, qs):
        q = qs.get("q", [""])[0]
        parent = re.search(r"'([^']+)' in parents", q).group(1)
        files = [f for f in self.drive.files.values()
                 if parent in f["parents"] and not f["trashed"]]
        name_m = re.search(r"name='((?:[^'\\]|\\.)*)'", q)
        if name_m:
            nm = name_m.group(1).replace("\\'", "'").replace("\\\\", "\\")
            files = [f for f in files if f["name"] == nm]
        out = {"files": [{k: f[k] for k in _META_KEYS} for f in files]}
        return 200, json.dumps(out).encode()

    def _upload(self, body: bytes):
        # multipart/related: <hdrs>\r\n\r\n{meta}\r\n--b\r\n<hdrs>\r\n\r\n<content>...
        _, rest = body.split(b"\r\n\r\n", 1)
        meta_raw, part2 = rest.split(b"\r\n--", 1)
        meta = json.loads(meta_raw)
        _, after = part2.split(b"\r\n\r\n", 1)
        content = after.rsplit(b"\r\n--", 1)[0]
        fid = self.drive.add(meta["name"], meta["parents"][0], content=content)
        return 200, json.dumps(self.drive.meta(fid)).encode()


def _api(drive):
    return DriveApi(FakeTransport(drive), token_provider=lambda: "tok")


def test_list_children_and_folder_flag():
    d = FakeDrive()
    d.add("readme.txt", "root", content=b"hi")
    d.add("photos", "root", mime=FOLDER_MIME)
    d.add("elsewhere.txt", "other")  # different parent
    api = _api(d)
    kids = api.list_children("root")
    by = {f.name: f for f in kids}
    assert set(by) == {"readme.txt", "photos"}
    assert by["photos"].is_dir and not by["readme.txt"].is_dir
    assert by["readme.txt"].size == 2


def test_find_child_prefers_folder():
    d = FakeDrive()
    d.add("dup", "root", content=b"x")            # a file named dup
    d.add("dup", "root", mime=FOLDER_MIME)        # a folder named dup
    found = _api(d).find_child("root", "dup")
    assert found is not None and found.is_dir      # folder wins for path descent


def test_find_child_missing_returns_none():
    assert _api(FakeDrive()).find_child("root", "nope") is None


def test_get_metadata_and_mtime_parsed():
    d = FakeDrive()
    fid = d.add("a.txt", "root", content=b"abc", modified="2026-07-27T10:00:00Z")
    f = _api(d).get(fid)
    assert f.name == "a.txt" and f.size == 3 and f.mtime > 0


def test_download_streams_content():
    d = FakeDrive()
    fid = d.add("blob.bin", "root", content=b"PAYLOAD")
    status, body = _api(d).download(fid)
    assert status == 200 and body.read() == b"PAYLOAD"


def test_create_folder():
    d = FakeDrive()
    folder = _api(d).create_folder("root", "newdir")
    assert folder.is_dir and folder.name == "newdir"
    assert any(f["name"] == "newdir" and f["mimeType"] == FOLDER_MIME
               for f in d.files.values())


def test_upload_multipart_roundtrip():
    d = FakeDrive()
    up = _api(d).upload("root", "up.txt", b"CONTENT")
    assert up.name == "up.txt"
    stored = next(f for f in d.files.values() if f["name"] == "up.txt")
    assert stored["_content"] == b"CONTENT"
    assert stored["parents"] == ["root"]


def test_rename():
    d = FakeDrive()
    fid = d.add("old.txt", "root")
    renamed = _api(d).rename(fid, "new.txt")
    assert renamed.name == "new.txt"
    assert d.files[fid]["name"] == "new.txt"


def test_delete():
    d = FakeDrive()
    fid = d.add("gone.txt", "root")
    _api(d).delete(fid)
    assert fid not in d.files


def test_error_maps_status_and_message():
    d = FakeDrive()
    with pytest.raises(DriveError) as ei:
        _api(d).get("missing")
    assert ei.value.status == 404
    assert "not found" in str(ei.value)


def test_drivefile_from_json_defaults():
    f = DriveFile.from_json({"id": "x", "name": "n", "mimeType": FOLDER_MIME})
    assert f.is_dir and f.size == 0 and f.mtime == 0.0


def test_export_streams_converted_content():
    d = FakeDrive()
    fid = d.add("Report", "root",
                mime="application/vnd.google-apps.document",
                export=b"DOCX-BYTES")
    status, body = _api(d).export(
        fid, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert status == 200 and body.read() == b"DOCX-BYTES"


def test_export_format_map():
    from dunders.fm.providers.gdrive.api import export_format
    assert export_format("application/vnd.google-apps.document")[1] == "docx"
    assert export_format("application/vnd.google-apps.spreadsheet")[1] == "xlsx"
    assert export_format("application/vnd.google-apps.presentation")[1] == "pptx"
    assert export_format("application/vnd.google-apps.anything")[1] == "pdf"
