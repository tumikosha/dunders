"""DriveApi — a small Google Drive REST v3 client over an injectable transport.

Network I/O is confined to :class:`HttpTransport`; ``DriveApi`` only builds
requests and parses responses, so a fake transport unit-tests every operation
offline. A ``token_provider`` callable returns a bearer token (refreshed by the
auth layer), keeping auth out of the request logic.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import BinaryIO, Protocol


__all__ = [
    "DriveApi", "DriveError", "DriveFile", "HttpTransport", "UrllibTransport",
    "FOLDER_MIME", "EXPORT_FORMATS", "export_format",
]

FOLDER_MIME = "application/vnd.google-apps.folder"
_OOXML = "application/vnd.openxmlformats-officedocument"
# Google-native doc mime -> (export mime, file extension). Docs/Sheets/Slides go
# to editable Office formats; anything else falls back to PDF.
EXPORT_FORMATS = {
    "application/vnd.google-apps.document": (f"{_OOXML}.wordprocessingml.document", "docx"),
    "application/vnd.google-apps.spreadsheet": (f"{_OOXML}.spreadsheetml.sheet", "xlsx"),
    "application/vnd.google-apps.presentation": (f"{_OOXML}.presentationml.presentation", "pptx"),
    "application/vnd.google-apps.drawing": ("image/png", "png"),
}
_EXPORT_FALLBACK = ("application/pdf", "pdf")


def export_format(native_mime: str) -> tuple[str, str]:
    """(export mime, extension) for a Google-native mime — PDF fallback."""
    return EXPORT_FORMATS.get(native_mime, _EXPORT_FALLBACK)
_API = "https://www.googleapis.com/drive/v3"
_UPLOAD = "https://www.googleapis.com/upload/drive/v3"
_FIELDS = "id,name,mimeType,size,modifiedTime,trashed"
_LIST_FIELDS = f"nextPageToken,files({_FIELDS})"


class DriveError(OSError):
    """A Drive API call failed. ``status`` is the HTTP code (0 if transport-level)."""

    def __init__(self, message: str, *, status: int = 0):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class DriveFile:
    """One Drive item's metadata (a file or a folder)."""

    id: str
    name: str
    is_dir: bool
    size: int
    mtime: float          # epoch seconds from modifiedTime (0.0 if absent)
    mime: str

    @classmethod
    def from_json(cls, d: dict) -> "DriveFile":
        mime = d.get("mimeType", "")
        try:
            size = int(d.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            is_dir=mime == FOLDER_MIME,
            size=size,
            mtime=_parse_rfc3339(d.get("modifiedTime")),
            mime=mime,
        )


def _parse_rfc3339(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        # e.g. "2026-07-27T10:11:12.345Z"
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v).astimezone(timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0.0


class HttpTransport(Protocol):
    """The only network surface DriveApi needs — easy to fake in tests."""

    def request(
        self, method: str, url: str, *,
        headers: dict[str, str], body: bytes | None = None,
    ) -> tuple[int, bytes]:
        """A buffered request → (status, body_bytes)."""
        ...

    def open(
        self, method: str, url: str, *, headers: dict[str, str],
    ) -> tuple[int, BinaryIO]:
        """A streaming request → (status, readable body) for downloads."""
        ...


class UrllibTransport:
    """stdlib ``urllib`` implementation of :class:`HttpTransport`."""

    def request(self, method, url, *, headers, body=None):
        req = urllib.request.Request(url, method=method, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:  # noqa: S310 (https only)
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:  # type: ignore[name-defined]
            return exc.code, exc.read()

    def open(self, method, url, *, headers):
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req)  # noqa: S310
            return resp.status, resp
        except urllib.error.HTTPError as exc:  # type: ignore[name-defined]
            return exc.code, exc


class DriveApi:
    """Drive REST v3 operations. ``token_provider()`` yields a bearer token."""

    def __init__(
        self,
        transport: HttpTransport,
        token_provider: Callable[[], str],
    ) -> None:
        self._t = transport
        self._token = token_provider

    # --- helpers -----------------------------------------------------------

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token()}"}
        if extra:
            h.update(extra)
        return h

    def _json(self, method: str, url: str, *, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = self._auth_headers(
            {"Content-Type": "application/json"} if body is not None else {}
        )
        status, raw = self._t.request(method, url, headers=headers, body=data)
        if status >= 400:
            raise self._error(status, raw)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise DriveError(f"bad JSON from Drive: {exc}", status=status) from exc

    @staticmethod
    def _error(status: int, raw: bytes) -> DriveError:
        detail = ""
        try:
            j = json.loads(raw)
            detail = j.get("error", {}).get("message", "") or j.get("error_description", "")
        except (ValueError, AttributeError):
            detail = raw[:200].decode("utf-8", "replace")
        return DriveError(f"Drive API {status}: {detail}", status=status)

    # --- reads -------------------------------------------------------------

    def list_children(self, parent_id: str) -> list[DriveFile]:
        """All non-trashed children of ``parent_id`` (follows pagination)."""
        out: list[DriveFile] = []
        page_token: str | None = None
        while True:
            params = {
                "q": f"'{parent_id}' in parents and trashed=false",
                "fields": _LIST_FIELDS,
                "pageSize": "1000",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            url = f"{_API}/files?{urllib.parse.urlencode(params)}"
            data = self._json("GET", url)
            out.extend(DriveFile.from_json(f) for f in data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return out

    def find_child(self, parent_id: str, name: str) -> DriveFile | None:
        """The first non-trashed child of ``parent_id`` named ``name`` (Drive
        allows duplicate names; first match wins, folders sorted first-ish)."""
        q = (
            f"'{parent_id}' in parents and trashed=false and "
            f"name='{_escape(name)}'"
        )
        params = {
            "q": q, "fields": _LIST_FIELDS, "pageSize": "10",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        url = f"{_API}/files?{urllib.parse.urlencode(params)}"
        files = [DriveFile.from_json(f) for f in self._json("GET", url).get("files", [])]
        if not files:
            return None
        # Prefer a folder match (so descending a name path is deterministic).
        files.sort(key=lambda f: not f.is_dir)
        return files[0]

    def get(self, file_id: str) -> DriveFile:
        params = {"fields": _FIELDS, "supportsAllDrives": "true"}
        url = f"{_API}/files/{file_id}?{urllib.parse.urlencode(params)}"
        return DriveFile.from_json(self._json("GET", url))

    def download(self, file_id: str) -> tuple[int, BinaryIO]:
        """Open a streaming read of a binary file's content (``alt=media``)."""
        params = {"alt": "media", "supportsAllDrives": "true"}
        url = f"{_API}/files/{file_id}?{urllib.parse.urlencode(params)}"
        status, body = self._t.open("GET", url, headers=self._auth_headers())
        if status >= 400:
            raise self._error(status, body.read())
        return status, body

    def export(self, file_id: str, mime: str) -> tuple[int, BinaryIO]:
        """Open a streaming read of a Google-native doc exported to ``mime``
        (Docs->docx, Sheets->xlsx, …) — these have no ``alt=media`` content."""
        params = {"mimeType": mime}
        url = f"{_API}/files/{file_id}/export?{urllib.parse.urlencode(params)}"
        status, body = self._t.open("GET", url, headers=self._auth_headers())
        if status >= 400:
            raise self._error(status, body.read())
        return status, body

    # --- writes ------------------------------------------------------------

    def create_folder(self, parent_id: str, name: str) -> DriveFile:
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        url = f"{_API}/files?{urllib.parse.urlencode({'fields': _FIELDS, 'supportsAllDrives': 'true'})}"
        return DriveFile.from_json(self._json("POST", url, body=body))

    def upload(self, parent_id: str, name: str, content: bytes,
               mime: str = "application/octet-stream") -> DriveFile:
        """Multipart upload of ``content`` as a new file under ``parent_id``."""
        meta = {"name": name, "parents": [parent_id]}
        boundary = "----dunders-gdrive-boundary"
        body = _multipart(meta, content, mime, boundary)
        params = {"uploadType": "multipart", "fields": _FIELDS,
                  "supportsAllDrives": "true"}
        url = f"{_UPLOAD}/files?{urllib.parse.urlencode(params)}"
        headers = self._auth_headers(
            {"Content-Type": f"multipart/related; boundary={boundary}"}
        )
        status, raw = self._t.request("POST", url, headers=headers, body=body)
        if status >= 400:
            raise self._error(status, raw)
        return DriveFile.from_json(json.loads(raw))

    def rename(self, file_id: str, new_name: str) -> DriveFile:
        url = f"{_API}/files/{file_id}?{urllib.parse.urlencode({'fields': _FIELDS, 'supportsAllDrives': 'true'})}"
        return DriveFile.from_json(self._json("PATCH", url, body={"name": new_name}))

    def delete(self, file_id: str) -> None:
        url = f"{_API}/files/{file_id}?{urllib.parse.urlencode({'supportsAllDrives': 'true'})}"
        self._json("DELETE", url)


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def _multipart(meta: dict, content: bytes, mime: str, boundary: str) -> bytes:
    dash = f"--{boundary}"
    parts = [
        dash,
        "Content-Type: application/json; charset=UTF-8",
        "",
        json.dumps(meta),
        dash,
        f"Content-Type: {mime}",
        "",
    ]
    head = ("\r\n".join(parts) + "\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + content + tail
