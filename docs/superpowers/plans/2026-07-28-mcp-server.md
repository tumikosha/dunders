# MCP Server over the dunders VFS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the dunders VFS (every bookmarked backend — local/sftp/ftp/zip/docker/db/gdrive) to an external LLM agent over MCP via a headless `dunders --mcp` process.

**Architecture:** New app-agnostic `dunders.mcp` package: a shared JSON-RPC protocol/transport/errors layer, a dynamic `MountTable` (bookmarks → mount points, mtime-reloaded), a Tools surface over the existing `VfsRegistry`, and an `McpServer` dispatcher. `main.py` gains a headless branch. Stdlib-only, hand-rolled JSON-RPC (matches the `dunders.ai` DNA). Server side + shared layer only; the MCP *client* provider is a later spec.

**Tech Stack:** Python ≥3.12 stdlib (`json`, `argparse`, `fnmatch`, `re`, `base64`, `importlib.metadata`), pytest (asyncio auto mode, but these tests are sync), ruff.

**Spec:** `docs/superpowers/specs/2026-07-28-mcp-server-design.md`

## Global Constraints

- **Stdlib-only.** `dunders.mcp` adds ZERO third-party dependencies. No `mcp` SDK, no pydantic/anyio.
- **Layering.** `dunders.mcp` imports only `dunders.core.vfs`, `dunders.config.bookmarks`, `dunders.fm.vfs_engine` (for `transfer`), `dunders.fm.actions`/`file_entry` types, and `dunders.ai.guardrails.is_ai_allowed`. It NEVER imports `dunders.app` or `dunders.windowing`.
- **stdout is protocol-only.** In stdio mode, all logs/diagnostics go to `sys.stderr`. A stray `print` to stdout corrupts framing.
- **Read-only by default.** Write tools are absent from `tools/list` unless `allow_write=True`. Not an in-tool check — they are not registered.
- **No secret leakage.** Bookmark passwords are used only for `resolve_target`; they are NEVER placed in any MCP response or stderr log.
- **MCP protocol version string:** `"2025-06-18"` (constant `PROTOCOL_VERSION`).
- **Read cap:** `READ_CAP = 1 << 20` (1 MiB) default for `read_file` and per-file `grep`.
- **Tests mirror source:** live under `tests/mcp/`. All offline, no network, using fake transports/providers.

---

## File Structure

- `dunders/mcp/__init__.py` — public entry `run_stdio(registry, table, *, allow_write)`.
- `dunders/mcp/protocol.py` — JSON-RPC 2.0 framing + envelope helpers + error codes (SHARED).
- `dunders/mcp/transport.py` — `Transport` protocol + `StdioTransport` (SHARED).
- `dunders/mcp/errors.py` — `McpError` + domain codes + `map_exception` (SHARED).
- `dunders/mcp/mounts.py` — `Mount` + `MountTable` (server side).
- `dunders/mcp/tools.py` — tool specs + handlers over the registry.
- `dunders/mcp/server.py` — `McpServer` dispatcher.
- `dunders/config/bookmarks.py` — MODIFY: `list_bookmarks(path=None)` + `bookmarks_mtime(path=None)`.
- `dunders/main.py` — MODIFY: `--mcp` flags + headless branch.
- Tests: `tests/mcp/test_protocol.py`, `test_transport.py`, `test_errors.py`, `test_mounts.py`, `test_tools.py`, `test_server.py`, `test_main.py`; MODIFY `tests/fm/test_bookmarks.py` (or add `tests/config/test_bookmarks.py`).

---

### Task 1: JSON-RPC protocol framing (`protocol.py`)

**Files:**
- Create: `dunders/mcp/__init__.py` (empty for now — package marker; real body added in Task 9)
- Create: `dunders/mcp/protocol.py`
- Test: `tests/mcp/__init__.py` (empty), `tests/mcp/test_protocol.py`

**Interfaces:**
- Produces:
  - `PROTOCOL_VERSION: str = "2025-06-18"`
  - Error codes: `PARSE_ERROR=-32700`, `INVALID_REQUEST=-32600`, `METHOD_NOT_FOUND=-32601`, `INVALID_PARAMS=-32602`, `INTERNAL_ERROR=-32603`
  - `class ProtocolError(Exception)` — raised on undecodable input.
  - `encode_message(obj: dict) -> bytes` — `json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"`.
  - `decode_message(line: bytes) -> dict` — parse one line; raise `ProtocolError` on bad JSON or non-object.
  - `make_response(msg_id, result: dict) -> dict`
  - `make_error(msg_id, code: int, message: str, data=None) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_protocol.py
import json
import pytest
from dunders.mcp import protocol as p


def test_encode_appends_newline_and_roundtrips():
    raw = p.encode_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert raw.endswith(b"\n")
    assert p.decode_message(raw) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_encode_is_utf8_not_ascii_escaped():
    raw = p.encode_message({"x": "papká"})
    assert "papká" in raw.decode("utf-8")


def test_decode_bad_json_raises_protocol_error():
    with pytest.raises(p.ProtocolError):
        p.decode_message(b"{not json}\n")


def test_decode_non_object_raises_protocol_error():
    with pytest.raises(p.ProtocolError):
        p.decode_message(b"[1, 2, 3]\n")


def test_make_response_and_error_shape():
    assert p.make_response(7, {"a": 1}) == {"jsonrpc": "2.0", "id": 7, "result": {"a": 1}}
    err = p.make_error(7, p.INVALID_PARAMS, "bad")
    assert err == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602, "message": "bad"}}
    err2 = p.make_error(7, p.INTERNAL_ERROR, "boom", data={"detail": "x"})
    assert err2["error"]["data"] == {"detail": "x"}


def test_protocol_version_constant():
    assert p.PROTOCOL_VERSION == "2025-06-18"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.mcp.protocol'`

- [ ] **Step 3: Write minimal implementation**

Create `dunders/mcp/__init__.py` empty. Create `tests/mcp/__init__.py` empty. Create `dunders/mcp/protocol.py`:

```python
"""JSON-RPC 2.0 framing for the MCP server/client (shared, stdlib-only).

One JSON object per line (LSP-style newline framing over a byte stream). This
module is transport-agnostic: it only turns dicts into bytes and back, plus
tiny envelope constructors. No I/O here.
"""

from __future__ import annotations

import json

__all__ = [
    "PROTOCOL_VERSION", "ProtocolError",
    "PARSE_ERROR", "INVALID_REQUEST", "METHOD_NOT_FOUND",
    "INVALID_PARAMS", "INTERNAL_ERROR",
    "encode_message", "decode_message", "make_response", "make_error",
]

PROTOCOL_VERSION = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class ProtocolError(Exception):
    """Input that is not a decodable single JSON-RPC object."""


def encode_message(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"


def decode_message(line: bytes) -> dict:
    try:
        obj = json.loads(line)
    except ValueError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("JSON-RPC message must be an object")
    return obj


def make_response(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_protocol.py -v` → Expected: PASS
Run: `ruff check dunders/mcp/protocol.py` → Expected: clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/__init__.py dunders/mcp/protocol.py tests/mcp/__init__.py tests/mcp/test_protocol.py
git commit -m "feat(mcp): JSON-RPC framing + envelope helpers"
```

---

### Task 2: Transport (`transport.py`)

**Files:**
- Create: `dunders/mcp/transport.py`
- Test: `tests/mcp/test_transport.py`

**Interfaces:**
- Consumes: `protocol.encode_message`, `protocol.decode_message`, `protocol.ProtocolError`.
- Produces:
  - `class Transport(Protocol)`: `read_message(self) -> dict | None` (None at EOF); `write_message(self, msg: dict) -> None`.
  - `class StdioTransport`: `__init__(self, reader: BinaryIO, writer: BinaryIO)`; `read_message`/`write_message`; classmethod `stdio() -> StdioTransport` binding `sys.stdin.buffer` / `sys.stdout.buffer`. A blank line is skipped; a `ProtocolError` line is skipped (logged to stderr), not fatal.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_transport.py
import io
from dunders.mcp import protocol as p
from dunders.mcp.transport import StdioTransport


def _reader(*msgs):
    buf = b"".join(p.encode_message(m) for m in msgs)
    return io.BytesIO(buf)


def test_read_messages_until_eof():
    r = _reader({"id": 1, "method": "a"}, {"id": 2, "method": "b"})
    t = StdioTransport(r, io.BytesIO())
    assert t.read_message() == {"id": 1, "method": "a"}
    assert t.read_message() == {"id": 2, "method": "b"}
    assert t.read_message() is None  # EOF


def test_write_message_frames_with_newline():
    w = io.BytesIO()
    StdioTransport(io.BytesIO(), w).write_message({"id": 3, "result": {}})
    assert w.getvalue() == p.encode_message({"id": 3, "result": {}})


def test_blank_line_skipped():
    r = io.BytesIO(b"\n" + p.encode_message({"id": 1, "method": "a"}))
    t = StdioTransport(r, io.BytesIO())
    assert t.read_message() == {"id": 1, "method": "a"}


def test_bad_line_skipped_not_fatal():
    r = io.BytesIO(b"{bad}\n" + p.encode_message({"id": 9, "method": "ok"}))
    t = StdioTransport(r, io.BytesIO())
    assert t.read_message() == {"id": 9, "method": "ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_transport.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.mcp.transport'`

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/transport.py
"""Transport layer for MCP framing. StdioTransport reads/writes newline-framed
JSON over two binary streams; stdout is protocol-only, diagnostics go to stderr.
"""

from __future__ import annotations

import sys
from typing import BinaryIO, Protocol

from dunders.mcp import protocol

__all__ = ["Transport", "StdioTransport"]


class Transport(Protocol):
    def read_message(self) -> dict | None: ...
    def write_message(self, msg: dict) -> None: ...


class StdioTransport:
    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self._r = reader
        self._w = writer

    @classmethod
    def stdio(cls) -> "StdioTransport":
        return cls(sys.stdin.buffer, sys.stdout.buffer)

    def read_message(self) -> dict | None:
        while True:
            line = self._r.readline()
            if not line:
                return None  # EOF
            if not line.strip():
                continue     # skip blank keepalive lines
            try:
                return protocol.decode_message(line)
            except protocol.ProtocolError as exc:
                print(f"mcp: dropping bad frame: {exc}", file=sys.stderr)
                continue

    def write_message(self, msg: dict) -> None:
        self._w.write(protocol.encode_message(msg))
        self._w.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_transport.py -v` → Expected: PASS
Run: `ruff check dunders/mcp/transport.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/transport.py tests/mcp/test_transport.py
git commit -m "feat(mcp): StdioTransport with newline framing"
```

---

### Task 3: Error mapping (`errors.py`)

**Files:**
- Create: `dunders/mcp/errors.py`
- Test: `tests/mcp/test_errors.py`

**Interfaces:**
- Produces:
  - Domain codes: `MOUNT_NOT_FOUND=-32001`, `PATH_NOT_FOUND=-32002`, `ACCESS_DENIED=-32003`, `PROVIDER_ERROR=-32010`.
  - `class McpError(Exception)`: `__init__(self, code: int, message: str, data=None)`; attrs `.code`, `.message`, `.data`.
  - `map_exception(exc: Exception) -> McpError` — `FileNotFoundError` → PATH_NOT_FOUND; `PermissionError` → ACCESS_DENIED; `McpError` → itself; anything else → PROVIDER_ERROR with a **generic** message (`"provider error"`) so no path/secret leaks.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_errors.py
import pytest
from dunders.mcp import errors as e


def test_mcperror_carries_code_message_data():
    err = e.McpError(e.MOUNT_NOT_FOUND, "no mount 'x'", data={"label": "x"})
    assert err.code == -32001 and err.message == "no mount 'x'"
    assert err.data == {"label": "x"}


def test_map_file_not_found():
    m = e.map_exception(FileNotFoundError("gone"))
    assert m.code == e.PATH_NOT_FOUND


def test_map_permission_error():
    assert e.map_exception(PermissionError()).code == e.ACCESS_DENIED


def test_map_passthrough_mcperror():
    orig = e.McpError(e.ACCESS_DENIED, "denied")
    assert e.map_exception(orig) is orig


def test_map_generic_hides_detail():
    m = e.map_exception(RuntimeError("secret sftp://user:pw@host failed"))
    assert m.code == e.PROVIDER_ERROR
    assert "pw" not in m.message and "sftp" not in m.message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_errors.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/errors.py
"""MCP domain errors + a mapper that hides provider internals (no secret leak).
Modeled on dunders.ai.providers._http.map_status.
"""

from __future__ import annotations

__all__ = [
    "McpError", "map_exception",
    "MOUNT_NOT_FOUND", "PATH_NOT_FOUND", "ACCESS_DENIED", "PROVIDER_ERROR",
]

MOUNT_NOT_FOUND = -32001
PATH_NOT_FOUND = -32002
ACCESS_DENIED = -32003
PROVIDER_ERROR = -32010


class McpError(Exception):
    def __init__(self, code: int, message: str, data=None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def map_exception(exc: Exception) -> McpError:
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, FileNotFoundError):
        return McpError(PATH_NOT_FOUND, "no such path")
    if isinstance(exc, PermissionError):
        return McpError(ACCESS_DENIED, "access denied")
    # Generic: never surface the raw message (may carry a URI with a password).
    return McpError(PROVIDER_ERROR, "provider error")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_errors.py -v` → PASS
Run: `ruff check dunders/mcp/errors.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/errors.py tests/mcp/test_errors.py
git commit -m "feat(mcp): domain error codes + leak-safe exception mapper"
```

---

### Task 4: Bookmarks — path param + mtime (`config/bookmarks.py`)

**Files:**
- Modify: `dunders/config/bookmarks.py`
- Test: Create `tests/config/__init__.py` (empty) + `tests/config/test_bookmarks_mcp.py`

**Interfaces:**
- Consumes: existing `bookmarks_path()`.
- Produces:
  - `list_bookmarks(path: Path | None = None) -> list[dict]` — reads `path` or the default; same validation/fault-tolerance as today.
  - `bookmarks_mtime(path: Path | None = None) -> float` — the file's mtime, or `0.0` if missing/unreadable.

- [ ] **Step 1: Write the failing test**

```python
# tests/config/test_bookmarks_mcp.py
import json
from dunders.config.bookmarks import list_bookmarks, bookmarks_mtime


def _write(p, items):
    p.write_text(json.dumps({"bookmarks": items}), encoding="utf-8")


def test_list_bookmarks_reads_explicit_path(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///tmp"}])
    got = list_bookmarks(f)
    assert got == [{"label": "a", "uri": "file:///tmp"}]


def test_list_bookmarks_missing_path_is_empty(tmp_path):
    assert list_bookmarks(tmp_path / "nope.json") == []


def test_bookmarks_mtime_zero_when_missing(tmp_path):
    assert bookmarks_mtime(tmp_path / "nope.json") == 0.0


def test_bookmarks_mtime_changes_on_write(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///tmp"}])
    m1 = bookmarks_mtime(f)
    import os
    os.utime(f, (m1 + 10, m1 + 10))
    assert bookmarks_mtime(f) > m1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/config/test_bookmarks_mcp.py -v`
Expected: FAIL (`list_bookmarks() takes 0 positional arguments` / no `bookmarks_mtime`)

- [ ] **Step 3: Write minimal implementation**

In `dunders/config/bookmarks.py`, change `list_bookmarks` and add `bookmarks_mtime`. Add `bookmarks_mtime` to `__all__`.

```python
def list_bookmarks(path: Path | None = None) -> list[dict]:
    """Every stored bookmark, or [] if the file is missing/corrupt."""
    target = path if path is not None else bookmarks_path()
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = data.get("bookmarks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [b for b in items if isinstance(b, dict) and "uri" in b and "label" in b]


def bookmarks_mtime(path: Path | None = None) -> float:
    """The bookmarks file mtime, or 0.0 if missing/unreadable."""
    target = path if path is not None else bookmarks_path()
    try:
        return target.stat().st_mtime
    except OSError:
        return 0.0
```

Update `__all__` to include `"bookmarks_mtime"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/config/test_bookmarks_mcp.py -v` → PASS
Run: `pytest tests/ -k bookmark -q` → Expected: existing bookmark tests still PASS (call sites use no args)
Run: `ruff check dunders/config/bookmarks.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/config/bookmarks.py tests/config/__init__.py tests/config/test_bookmarks_mcp.py
git commit -m "feat(config): list_bookmarks(path) + bookmarks_mtime for MCP mounts"
```

---

### Task 5: MountTable (`mounts.py`)

**Files:**
- Create: `dunders/mcp/mounts.py`
- Test: `tests/mcp/test_mounts.py`

**Interfaces:**
- Consumes: `VfsPath.parse`, `VfsRegistry.resolve`, `config.bookmarks.list_bookmarks`/`bookmarks_mtime`, `errors.McpError`/codes, `dunders.ai.guardrails.is_ai_allowed`.
- Produces:
  - `@dataclass(frozen=True) class Mount: label: str; loc: VfsPath; scheme: str; display: str; password: str | None`
  - `class MountTable`:
    - `__init__(self, registry, *, path=None, allow: set[str] | None = None, noai_globs: tuple[str, ...] = ())`
    - `mounts(self) -> list[Mount]` — reload-if-changed, return current mounts (sorted by label).
    - `get(self, label: str) -> Mount` — reload; raise `McpError(MOUNT_NOT_FOUND, ...)` if absent.
    - `resolve(self, label: str, path: str) -> VfsPath` — mount root loc descended by clean `path` segments; raise `McpError(ACCESS_DENIED, ...)` on traversal (`..`, absolute, empty-after-strip is root).
    - `connected(self, label: str) -> bool`
    - `ensure_connected(self, mount: Mount) -> None` — lazily `resolve_target` a slow provider once; cache the label.
- Reload semantics: keyed on `bookmarks_mtime`. On reload, `_connected` labels that vanished or whose loc changed are dropped.
- No-AI: a `file`-scheme mount whose `to_local()` path is disallowed by `is_ai_allowed(path, cloud=True, globs=noai_globs)` is excluded. Non-file schemes have no local `.dunders-noai` marker → always included (documented scope).

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_mounts.py
import json
import pytest
from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.mcp.mounts import Mount, MountTable
from dunders.mcp import errors as e


class FakeProvider:
    """A provider that records resolve_target and answers is_dir/scan trivially."""
    def __init__(self, scheme, *, slow=False):
        self.scheme = scheme
        self.capabilities = frozenset({"read", "write"} | ({"slow"} if slow else set()))
        self.resolved = []
    def resolve_target(self, spec, *, base, password=None):
        self.resolved.append((spec, password))
        return base
    def scan(self, loc, *, show_hidden=False, include_parent=True):
        return []
    def is_dir(self, loc):
        return True


def _registry():
    reg = VfsRegistry()
    reg.register(FakeProvider("file"))
    reg.register(FakeProvider("sftp", slow=True))
    return reg


def _write(p, items):
    p.write_text(json.dumps({"bookmarks": items}), encoding="utf-8")


def test_mounts_built_from_file(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [
        {"label": "home", "uri": "file:///home/me"},
        {"label": "prod", "uri": "sftp://me@host!/srv", "password": "pw"},
    ])
    table = MountTable(_registry(), path=f)
    labels = {m.label for m in table.mounts()}
    assert labels == {"home", "prod"}


def test_mcp_false_bookmark_skipped(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [
        {"label": "shown", "uri": "file:///a"},
        {"label": "hidden", "uri": "file:///b", "mcp": False},
    ])
    assert {m.label for m in MountTable(_registry(), path=f).mounts()} == {"shown"}


def test_allow_narrows(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///a"}, {"label": "b", "uri": "file:///b"}])
    table = MountTable(_registry(), path=f, allow={"a"})
    assert {m.label for m in table.mounts()} == {"a"}


def test_get_unknown_raises_mount_not_found(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///a"}])
    table = MountTable(_registry(), path=f)
    with pytest.raises(e.McpError) as ei:
        table.get("nope")
    assert ei.value.code == e.MOUNT_NOT_FOUND


def test_resolve_descends_clean_path(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    loc = table.resolve("prod", "app/config")
    assert loc.scheme == "sftp" and loc.parts == ("srv", "app", "config")


def test_resolve_traversal_rejected(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    for bad in ["../etc", "a/../../b", "/abs"]:
        with pytest.raises(e.McpError) as ei:
            table.resolve("prod", bad)
        assert ei.value.code == e.ACCESS_DENIED


def test_empty_path_is_mount_root(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    assert table.resolve("prod", "").parts == ("srv",)


def test_lazy_connect_calls_resolve_target_once(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv", "password": "pw"}])
    reg = _registry()
    table = MountTable(reg, path=f)
    m = table.get("prod")
    assert not table.connected("prod")
    table.ensure_connected(m)
    table.ensure_connected(m)  # idempotent
    prov = reg.resolve(m.loc)
    assert prov.resolved == [("me@host/srv", "pw")]  # connected exactly once
    assert table.connected("prod")


def test_reload_on_mtime_change_adds_and_drops(tmp_path):
    import os
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "sftp://me@host!/srv"}])
    reg = _registry()
    table = MountTable(reg, path=f)
    table.ensure_connected(table.get("a"))
    assert table.connected("a")
    # rewrite: drop 'a', add 'b'; bump mtime
    _write(f, [{"label": "b", "uri": "file:///b"}])
    m = table_mtime = f.stat().st_mtime
    os.utime(f, (m + 10, m + 10))
    assert {mt.label for mt in table.mounts()} == {"b"}
    assert not table.connected("a")  # vanished label's connect flag dropped


def test_noai_file_mount_hidden(tmp_path):
    (tmp_path / ".dunders-noai").write_text("")
    secret = tmp_path / "secret"
    secret.mkdir()
    f = tmp_path / "bm.json"
    _write(f, [{"label": "secret", "uri": secret.as_uri()},
               {"label": "ok", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    # local mount under a .dunders-noai marker is excluded; remote stays
    assert {m.label for m in table.mounts()} == {"ok"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_mounts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.mcp.mounts'`

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/mounts.py
"""MountTable — projects bookmarks onto MCP mount points.

The bookmarks file on disk is the single source of truth; the table re-reads it
when its mtime changes, so mounts reconfigure live without a server restart.
Slow (network) providers connect lazily via resolve_target on first access.
"""

from __future__ import annotations

from dataclasses import dataclass

from dunders.ai.guardrails import is_ai_allowed
from dunders.config.bookmarks import bookmarks_mtime, bookmarks_path, list_bookmarks
from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.mcp import errors

__all__ = ["Mount", "MountTable"]


@dataclass(frozen=True)
class Mount:
    label: str
    loc: VfsPath
    scheme: str
    display: str
    password: str | None


class MountTable:
    def __init__(
        self, registry: VfsRegistry, *, path=None,
        allow: set[str] | None = None, noai_globs: tuple[str, ...] = (),
    ) -> None:
        self._registry = registry
        self._path = path if path is not None else bookmarks_path()
        self._allow = allow
        self._noai_globs = noai_globs
        self._mounts: dict[str, Mount] = {}
        self._connected: set[str] = set()
        self._mtime = -1.0
        self._reload(force=True)

    # --- loading / reload --------------------------------------------------

    def _reload(self, *, force: bool = False) -> None:
        mtime = bookmarks_mtime(self._path)
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        fresh: dict[str, Mount] = {}
        for bm in list_bookmarks(self._path):
            label = bm["label"]
            if self._allow is not None and label not in self._allow:
                continue
            if bm.get("mcp") is False:
                continue
            try:
                loc = VfsPath.parse(bm["uri"])
            except ValueError:
                continue
            if not self._ai_ok(loc):
                continue
            fresh[label] = Mount(
                label=label, loc=loc, scheme=loc.scheme,
                display=loc.display(), password=bm.get("password"),
            )
        # Drop connect flags for labels that vanished or whose loc changed.
        self._connected = {
            lbl for lbl in self._connected
            if lbl in fresh and fresh[lbl].loc == self._mounts.get(lbl, fresh[lbl]).loc
        }
        self._mounts = fresh

    def _ai_ok(self, loc: VfsPath) -> bool:
        if loc.scheme != "file":
            return True  # no local .dunders-noai marker to consult
        try:
            return is_ai_allowed(loc.to_local(), cloud=True, globs=self._noai_globs)
        except ValueError:
            return True

    # --- queries -----------------------------------------------------------

    def mounts(self) -> list[Mount]:
        self._reload()
        return [self._mounts[k] for k in sorted(self._mounts)]

    def get(self, label: str) -> Mount:
        self._reload()
        try:
            return self._mounts[label]
        except KeyError:
            raise errors.McpError(
                errors.MOUNT_NOT_FOUND, f"no mount {label!r}"
            ) from None

    def resolve(self, label: str, path: str) -> VfsPath:
        mount = self.get(label)
        loc = mount.loc
        for seg in path.split("/"):
            if seg in ("", "."):
                continue
            if seg == ".." or "/" in seg or seg.startswith("\\"):
                raise errors.McpError(
                    errors.ACCESS_DENIED, "path escapes mount root"
                )
            loc = loc.child(seg)
        if path.startswith("/"):
            raise errors.McpError(errors.ACCESS_DENIED, "absolute path rejected")
        return loc

    # --- lazy connection ---------------------------------------------------

    def connected(self, label: str) -> bool:
        return label in self._connected

    def ensure_connected(self, mount: Mount) -> None:
        if mount.label in self._connected:
            return
        provider = self._registry.resolve(mount.loc)
        if "slow" not in getattr(provider, "capabilities", frozenset()):
            self._connected.add(mount.label)
            return
        resolver = getattr(provider, "resolve_target", None)
        if callable(resolver):
            spec = self._spec_for(mount.loc)
            resolver(spec, base=mount.loc, password=mount.password)
        self._connected.add(mount.label)

    @staticmethod
    def _spec_for(loc: VfsPath) -> str:
        # Mirror app._open_bookmark: a root that is itself a URL reopens verbatim;
        # host/path providers keep their in-source path suffix.
        if "://" in loc.root:
            return loc.root
        return loc.root + ("/" + "/".join(loc.parts) if loc.parts else "/")
```

**Note on the traversal test `/abs`:** `"/abs".split("/")` → `["", "abs"]`; the empty first segment is skipped, `abs` appended — so the explicit `path.startswith("/")` check after the loop raises ACCESS_DENIED. Keep that check.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_mounts.py -v` → PASS
Run: `ruff check dunders/mcp/mounts.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/mounts.py tests/mcp/test_mounts.py
git commit -m "feat(mcp): dynamic MountTable (mtime reload, traversal guard, lazy connect)"
```

---

### Task 6: Read tools (`tools.py` — read half)

**Files:**
- Create: `dunders/mcp/tools.py`
- Test: `tests/mcp/test_tools.py`

**Interfaces:**
- Consumes: `MountTable`, `VfsRegistry`, `FileEntry`, `errors`.
- Produces:
  - `READ_CAP = 1 << 20`
  - `def tool_specs(allow_write: bool) -> list[dict]` — MCP `tools/list` payload (`name`, `description`, `inputSchema`). Read tools always; write tools appended only when `allow_write`.
  - `def call_tool(name: str, args: dict, *, table: MountTable, registry: VfsRegistry, allow_write: bool) -> dict` — dispatch to a handler; unknown or write-while-disabled → `McpError(errors.PROVIDER_ERROR? no)` — use a dedicated `METHOD_NOT_FOUND` from protocol. Import `from dunders.mcp.protocol import METHOD_NOT_FOUND` and raise `McpError(METHOD_NOT_FOUND, f"unknown tool {name!r}")`.
  - Read handlers return plain JSON-able dicts:
    - `list_mounts()` → `{"mounts": [{"label","scheme","display","connected"}...]}`
    - `list_dir(mount, path="")` → `{"entries": [{"name","is_dir","size","mtime"}...]}`
    - `stat(mount, path)` → `{"name","is_dir","size","mtime"}`
    - `read_file(mount, path, offset=0, length=null)` → `{"encoding": "text"|"base64", "data": str, "eof": bool}`
    - `search(mount, path, glob, max=200)` → `{"matches": [relpath...], "truncated": bool}`
    - `grep(mount, path, pattern, glob="*", ignore_case=false, max_matches=200, max_file_bytes=READ_CAP)` → `{"matches": [{"path","line","text"}...], "truncated": bool}`

This task implements `tool_specs` (read entries + a placeholder empty write list when `allow_write` — extended in Task 7), `call_tool` dispatch, and the six read handlers.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_tools.py
import base64
import json
import pytest
from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.fm.vfs_local import LocalProvider
from dunders.mcp.mounts import MountTable
from dunders.mcp import tools, errors


def _local_table(tmp_path, extra=None):
    f = tmp_path / "bm.json"
    items = [{"label": "root", "uri": (tmp_path / "data").as_uri()}]
    if extra:
        items += extra
    f.write_text(json.dumps({"bookmarks": items}), encoding="utf-8")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    return MountTable(reg, path=f), reg


def _seed(tmp_path):
    d = tmp_path / "data"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("hello\nworld\n")
    (d / "sub" / "b.txt").write_text("needle here\n")
    (d / "bin.dat").write_bytes(b"\x00\x01\x02BIN")
    return d


def test_list_mounts(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("list_mounts", {}, table=table, registry=reg, allow_write=False)
    labels = {m["label"] for m in out["mounts"]}
    assert labels == {"root"}
    assert out["mounts"][0]["scheme"] == "file"


def test_list_dir(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("list_dir", {"mount": "root", "path": ""},
                          table=table, registry=reg, allow_write=False)
    names = {e["name"]: e for e in out["entries"]}
    assert set(names) == {"sub", "a.txt", "bin.dat"}
    assert names["sub"]["is_dir"] and not names["a.txt"]["is_dir"]
    assert names["a.txt"]["size"] == 12


def test_read_file_text(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file", {"mount": "root", "path": "a.txt"},
                          table=table, registry=reg, allow_write=False)
    assert out["encoding"] == "text" and out["data"] == "hello\nworld\n"


def test_read_file_binary_base64(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file", {"mount": "root", "path": "bin.dat"},
                          table=table, registry=reg, allow_write=False)
    assert out["encoding"] == "base64"
    assert base64.b64decode(out["data"]) == b"\x00\x01\x02BIN"


def test_read_file_offset_and_cap(tmp_path):
    d = _seed(tmp_path)
    (d / "big.txt").write_text("X" * (tools.READ_CAP + 500))
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("read_file",
                          {"mount": "root", "path": "big.txt", "offset": 10},
                          table=table, registry=reg, allow_write=False)
    assert len(out["data"]) == tools.READ_CAP  # capped
    assert out["eof"] is False


def test_stat(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("stat", {"mount": "root", "path": "sub"},
                          table=table, registry=reg, allow_write=False)
    assert out["name"] == "sub" and out["is_dir"] is True


def test_search_glob(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("search", {"mount": "root", "path": "", "glob": "*.txt"},
                          table=table, registry=reg, allow_write=False)
    assert set(out["matches"]) == {"a.txt", "sub/b.txt"}


def test_grep_content(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("grep",
                          {"mount": "root", "path": "", "pattern": "needle"},
                          table=table, registry=reg, allow_write=False)
    assert len(out["matches"]) == 1
    hit = out["matches"][0]
    assert hit["path"] == "sub/b.txt" and hit["line"] == 1 and "needle" in hit["text"]


def test_grep_skips_binary(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("grep",
                          {"mount": "root", "path": "", "pattern": "BIN"},
                          table=table, registry=reg, allow_write=False)
    assert out["matches"] == []  # bin.dat skipped (NUL sniff)


def test_specs_read_only_has_no_write_tools(tmp_path):
    names = {t["name"] for t in tools.tool_specs(allow_write=False)}
    assert {"list_mounts", "list_dir", "read_file", "stat", "search", "grep"} <= names
    assert "write_file" not in names and "delete" not in names


def test_unknown_tool_raises(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    with pytest.raises(errors.McpError):
        tools.call_tool("nope", {}, table=table, registry=reg, allow_write=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.mcp.tools'`

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/tools.py
"""MCP tool specs + handlers over the VfsRegistry. Read tools always present;
write tools appended only when allow_write (see WRITE_TOOLS in the write half).
"""

from __future__ import annotations

import base64
import fnmatch
import re

from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.mcp import errors
from dunders.mcp.mounts import MountTable
from dunders.mcp.protocol import METHOD_NOT_FOUND

__all__ = ["READ_CAP", "tool_specs", "call_tool"]

READ_CAP = 1 << 20  # 1 MiB


# --- schema fragments ------------------------------------------------------
_MOUNT = {"type": "string", "description": "bookmark label (a mount point)"}
_PATH = {"type": "string", "description": "'/'-relative path inside the mount"}


def _read_specs() -> list[dict]:
    return [
        {"name": "list_mounts",
         "description": "List available mount points (bookmarks).",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_dir",
         "description": "List one directory in a mount.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH},
                         "required": ["mount"]}},
        {"name": "read_file",
         "description": "Read a file (text or base64), capped at 1 MiB.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH,
                                        "offset": {"type": "integer"},
                                        "length": {"type": "integer"}},
                         "required": ["mount", "path"]}},
        {"name": "stat",
         "description": "Metadata for one item.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH},
                         "required": ["mount", "path"]}},
        {"name": "search",
         "description": "Recursive name (glob) search.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH,
                                        "glob": {"type": "string"},
                                        "max": {"type": "integer"}},
                         "required": ["mount", "glob"]}},
        {"name": "grep",
         "description": "Recursive content (regex) search.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH,
                                        "pattern": {"type": "string"},
                                        "glob": {"type": "string"},
                                        "ignore_case": {"type": "boolean"},
                                        "max_matches": {"type": "integer"},
                                        "max_file_bytes": {"type": "integer"}},
                         "required": ["mount", "pattern"]}},
    ]


def tool_specs(allow_write: bool) -> list[dict]:
    specs = _read_specs()
    if allow_write:
        from dunders.mcp.tools_write import write_specs  # Task 7
        specs += write_specs()
    return specs


def call_tool(name, args, *, table, registry, allow_write) -> dict:
    handler = _READ_HANDLERS.get(name)
    if handler is not None:
        return handler(args, table, registry)
    if allow_write:
        from dunders.mcp.tools_write import WRITE_HANDLERS  # Task 7
        w = WRITE_HANDLERS.get(name)
        if w is not None:
            return w(args, table, registry)
    raise errors.McpError(METHOD_NOT_FOUND, f"unknown tool {name!r}")


# --- helpers ---------------------------------------------------------------

def _entry_dict(e) -> dict:
    return {"name": e.name, "is_dir": e.is_dir, "size": e.size, "mtime": e.mtime}


def _iter_tree(registry, loc):
    """Yield (entry, relparts) for every non-dir under loc, recursing dirs."""
    provider = registry.resolve(loc)
    stack = [(loc, ())]
    while stack:
        cur, rel = stack.pop()
        for e in provider.scan(cur, show_hidden=True, include_parent=False):
            childrel = (*rel, e.name)
            if e.is_dir:
                stack.append((e.loc, childrel))
            else:
                yield e, childrel


# --- read handlers ---------------------------------------------------------

def _h_list_mounts(args, table: MountTable, registry) -> dict:
    return {"mounts": [
        {"label": m.label, "scheme": m.scheme, "display": m.display,
         "connected": table.connected(m.label)}
        for m in table.mounts()
    ]}


def _h_list_dir(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    loc = table.resolve(args["mount"], args.get("path", ""))
    provider = registry.resolve(loc)
    rows = provider.scan(loc, show_hidden=True, include_parent=False)
    return {"entries": [_entry_dict(e) for e in rows]}


def _h_stat(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    path = args["path"]
    loc = table.resolve(args["mount"], path)
    if loc == mount.loc:  # mount root
        return {"name": mount.label, "is_dir": True, "size": 0, "mtime": 0.0}
    parent = table.resolve(args["mount"], "/".join(path.split("/")[:-1]))
    provider = registry.resolve(loc)
    for e in provider.scan(parent, show_hidden=True, include_parent=False):
        if e.name == loc.name:
            return _entry_dict(e)
    raise errors.McpError(errors.PATH_NOT_FOUND, "no such path")


def _h_read_file(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    loc = table.resolve(args["mount"], args["path"])
    provider = registry.resolve(loc)
    offset = max(0, int(args.get("offset", 0)))
    length = args.get("length")
    cap = READ_CAP if length is None else min(int(length), READ_CAP)
    with provider.open_read(loc) as fh:
        if offset:
            _skip(fh, offset)
        data = fh.read(cap)
        eof = not fh.read(1)
    try:
        return {"encoding": "text", "data": data.decode("utf-8"), "eof": eof}
    except UnicodeDecodeError:
        return {"encoding": "base64",
                "data": base64.b64encode(data).decode("ascii"), "eof": eof}


def _skip(fh, n: int) -> None:
    try:
        fh.seek(n)
        return
    except (OSError, ValueError, AttributeError):
        pass
    remaining = n
    while remaining > 0:
        chunk = fh.read(min(remaining, 1 << 16))
        if not chunk:
            break
        remaining -= len(chunk)


def _h_search(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    root = table.resolve(args["mount"], args.get("path", ""))
    glob = args["glob"]
    limit = int(args.get("max", 200))
    out = []
    for _e, rel in _iter_tree(registry, root):
        if fnmatch.fnmatch(rel[-1], glob):
            out.append("/".join(rel))
            if len(out) >= limit:
                return {"matches": out, "truncated": True}
    return {"matches": out, "truncated": False}


def _h_grep(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    root = table.resolve(args["mount"], args.get("path", ""))
    provider = registry.resolve(root)
    glob = args.get("glob", "*")
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    rx = re.compile(args["pattern"], flags)
    max_matches = int(args.get("max_matches", 200))
    max_bytes = int(args.get("max_file_bytes", READ_CAP))
    out = []
    for e, rel in _iter_tree(registry, root):
        if not fnmatch.fnmatch(rel[-1], glob) or e.size > max_bytes:
            continue
        with provider.open_read(e.loc) as fh:
            blob = fh.read(max_bytes)
        if b"\x00" in blob[:8192]:
            continue  # binary
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                out.append({"path": "/".join(rel), "line": i, "text": line})
                if len(out) >= max_matches:
                    return {"matches": out, "truncated": True}
    return {"matches": out, "truncated": False}


_READ_HANDLERS = {
    "list_mounts": _h_list_mounts,
    "list_dir": _h_list_dir,
    "read_file": _h_read_file,
    "stat": _h_stat,
    "search": _h_search,
    "grep": _h_grep,
}
```

**Note:** `tool_specs(allow_write=True)` and the write dispatch import `dunders.mcp.tools_write` (Task 7). Until Task 7 lands, only call `tool_specs(allow_write=False)` / `allow_write=False` in tests — the read tests above all pass `allow_write=False`, so the deferred import is never hit.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_tools.py -v` → PASS
Run: `ruff check dunders/mcp/tools.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/tools.py tests/mcp/test_tools.py
git commit -m "feat(mcp): read tools (list_mounts/list_dir/read_file/stat/search/grep)"
```

---

### Task 7: Write tools (`tools_write.py`)

**Files:**
- Create: `dunders/mcp/tools_write.py`
- Test: extend `tests/mcp/test_tools.py`

**Interfaces:**
- Consumes: `MountTable`, `VfsRegistry`, `transfer` from `dunders.fm.vfs_engine`, `errors`.
- Produces:
  - `def write_specs() -> list[dict]` — specs for `write_file`, `mkdir`, `delete`, `copy`.
  - `WRITE_HANDLERS: dict[str, callable]` — `(args, table, registry) -> dict`.
  - `write_file(mount, path, content?|content_base64?, overwrite=false)` → `{"written": int}`
  - `mkdir(mount, path)` → `{"created": path}` (via `provider.mkdir(parent, name)`)
  - `delete(mount, path)` → `{"deleted": path}` (via `provider.delete([loc])`)
  - `copy(src_mount, src_path, dst_mount, dst_path)` — `dst_path` is a **directory** in the destination mount; keeps the source basename. Via `transfer(registry, [src_loc], dst_dir_loc, mode="copy")`. → `{"copied": basename}`
  - A handler that gets an `OpResult` with `errors` raises `McpError(PROVIDER_ERROR, "write failed")`.

- [ ] **Step 1: Write the failing test** (append to `tests/mcp/test_tools.py`)

```python
def test_write_tools_present_only_with_allow_write():
    names = {t["name"] for t in tools.tool_specs(allow_write=True)}
    assert {"write_file", "mkdir", "delete", "copy"} <= names


def test_write_file_creates(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    out = tools.call_tool("write_file",
                          {"mount": "root", "path": "new.txt", "content": "hi"},
                          table=table, registry=reg, allow_write=True)
    assert out["written"] == 2
    assert (tmp_path / "data" / "new.txt").read_text() == "hi"


def test_write_tool_absent_when_disabled(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    with pytest.raises(errors.McpError):
        tools.call_tool("write_file", {"mount": "root", "path": "x", "content": "y"},
                        table=table, registry=reg, allow_write=False)


def test_mkdir_and_delete(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    tools.call_tool("mkdir", {"mount": "root", "path": "fresh"},
                    table=table, registry=reg, allow_write=True)
    assert (tmp_path / "data" / "fresh").is_dir()
    tools.call_tool("delete", {"mount": "root", "path": "a.txt"},
                    table=table, registry=reg, allow_write=True)
    assert not (tmp_path / "data" / "a.txt").exists()


def test_copy_within_mount(tmp_path):
    _seed(tmp_path)
    table, reg = _local_table(tmp_path)
    tools.call_tool("copy",
                    {"src_mount": "root", "src_path": "a.txt",
                     "dst_mount": "root", "dst_path": "sub"},
                    table=table, registry=reg, allow_write=True)
    assert (tmp_path / "data" / "sub" / "a.txt").read_text() == "hello\nworld\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_tools.py -k write -v` and `-k copy -v`
Expected: FAIL (`ModuleNotFoundError: dunders.mcp.tools_write`)

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/tools_write.py
"""Write-side MCP tools. Imported lazily by tools.py only when allow_write."""

from __future__ import annotations

import base64

from dunders.fm.vfs_engine import transfer
from dunders.mcp import errors
from dunders.mcp.mounts import MountTable

__all__ = ["write_specs", "WRITE_HANDLERS"]

_MOUNT = {"type": "string"}
_PATH = {"type": "string"}


def write_specs() -> list[dict]:
    return [
        {"name": "write_file",
         "description": "Create/overwrite a file with text or base64 content.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH,
                                        "content": {"type": "string"},
                                        "content_base64": {"type": "string"},
                                        "overwrite": {"type": "boolean"}},
                         "required": ["mount", "path"]}},
        {"name": "mkdir",
         "description": "Create a directory.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH},
                         "required": ["mount", "path"]}},
        {"name": "delete",
         "description": "Delete a file or directory.",
         "inputSchema": {"type": "object",
                         "properties": {"mount": _MOUNT, "path": _PATH},
                         "required": ["mount", "path"]}},
        {"name": "copy",
         "description": "Copy a file/dir into a destination directory (cross-mount ok).",
         "inputSchema": {"type": "object",
                         "properties": {"src_mount": _MOUNT, "src_path": _PATH,
                                        "dst_mount": _MOUNT, "dst_path": _PATH},
                         "required": ["src_mount", "src_path", "dst_mount", "dst_path"]}},
    ]


def _bytes_from(args) -> bytes:
    if "content_base64" in args and args["content_base64"] is not None:
        return base64.b64decode(args["content_base64"])
    return args.get("content", "").encode("utf-8")


def _w_write_file(args, table: MountTable, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    loc = table.resolve(args["mount"], args["path"])
    provider = registry.resolve(loc)
    data = _bytes_from(args)
    with provider.open_write(loc, overwrite=bool(args.get("overwrite", False))) as w:
        w.write(data)
    return {"written": len(data)}


def _w_mkdir(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    loc = table.resolve(args["mount"], args["path"])
    provider = registry.resolve(loc)
    result = provider.mkdir(loc.parent, loc.name)
    if result.errors:
        raise errors.McpError(errors.PROVIDER_ERROR, "mkdir failed")
    return {"created": args["path"]}


def _w_delete(args, table, registry) -> dict:
    mount = table.get(args["mount"])
    table.ensure_connected(mount)
    loc = table.resolve(args["mount"], args["path"])
    provider = registry.resolve(loc)
    result = provider.delete([loc])
    if result.errors:
        raise errors.McpError(errors.PROVIDER_ERROR, "delete failed")
    return {"deleted": args["path"]}


def _w_copy(args, table, registry) -> dict:
    src_mount = table.get(args["src_mount"])
    dst_mount = table.get(args["dst_mount"])
    table.ensure_connected(src_mount)
    table.ensure_connected(dst_mount)
    src = table.resolve(args["src_mount"], args["src_path"])
    dst_dir = table.resolve(args["dst_mount"], args["dst_path"])
    result = transfer(registry, [src], dst_dir, mode="copy")
    if result.errors:
        raise errors.McpError(errors.PROVIDER_ERROR, "copy failed")
    return {"copied": src.name}


WRITE_HANDLERS = {
    "write_file": _w_write_file,
    "mkdir": _w_mkdir,
    "delete": _w_delete,
    "copy": _w_copy,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_tools.py -v` → PASS (read + write)
Run: `ruff check dunders/mcp/tools_write.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/tools_write.py tests/mcp/test_tools.py
git commit -m "feat(mcp): write tools (write_file/mkdir/delete/copy) behind allow_write"
```

---

### Task 8: Server dispatcher (`server.py`)

**Files:**
- Create: `dunders/mcp/server.py`
- Test: `tests/mcp/test_server.py`

**Interfaces:**
- Consumes: `protocol` (envelopes, codes, `PROTOCOL_VERSION`), `Transport`, `tools.tool_specs`/`call_tool`, `errors.McpError`/`map_exception`, `MountTable`, `VfsRegistry`.
- Produces:
  - `class McpServer`: `__init__(self, registry, table, *, allow_write: bool)`; `serve(self, transport: Transport) -> None` (loop until EOF); `handle(self, msg: dict) -> dict | None` (pure dispatch; `None` for notifications, used directly by tests).
  - `initialize` → `{"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}, "serverInfo": {"name": "dunders", "version": _version()}}`
  - `notifications/initialized` → `None`
  - `tools/list` → `{"tools": tool_specs(allow_write)}`
  - `tools/call` → `{"content": [{"type": "text", "text": json.dumps(result)}]}`
  - `McpError` → `make_error(id, code, message, data)`; unknown method → `make_error(id, METHOD_NOT_FOUND, ...)`; any other exception caught → `map_exception` → error (server never crashes).

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_server.py
import io
import json
import pytest
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
from dunders.mcp import protocol as p
from dunders.mcp.mounts import MountTable
from dunders.mcp.server import McpServer
from dunders.mcp.transport import StdioTransport


def _table(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("hi")
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": [
        {"label": "root", "uri": (tmp_path / "data").as_uri()}]}), encoding="utf-8")
    reg = VfsRegistry()
    reg.register(LocalProvider())
    return reg, MountTable(reg, path=f)


def _req(mid, method, params=None):
    m = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        m["params"] = params
    return m


def test_initialize_handshake(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(1, "initialize", {}))
    assert resp["result"]["protocolVersion"] == p.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "dunders"
    assert "tools" in resp["result"]["capabilities"]


def test_notifications_initialized_returns_none(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    assert srv.handle(_req(None, "notifications/initialized")) is None


def test_tools_list_read_only(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    names = {t["name"] for t in srv.handle(_req(2, "tools/list"))["result"]["tools"]}
    assert "read_file" in names and "write_file" not in names


def test_tools_list_write_enabled(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=True)
    names = {t["name"] for t in srv.handle(_req(2, "tools/list"))["result"]["tools"]}
    assert "write_file" in names


def test_tools_call_read_file(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(3, "tools/call",
                           {"name": "read_file",
                            "arguments": {"mount": "root", "path": "a.txt"}}))
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["data"] == "hi"


def test_unknown_method_is_error_not_crash(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(4, "no/such/method"))
    assert resp["error"]["code"] == p.METHOD_NOT_FOUND


def test_tools_call_unknown_mount_maps_error(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    resp = srv.handle(_req(5, "tools/call",
                           {"name": "list_dir", "arguments": {"mount": "ghost"}}))
    assert resp["error"]["code"] == -32001  # MOUNT_NOT_FOUND


def test_serve_loop_over_transport(tmp_path):
    reg, table = _table(tmp_path)
    srv = McpServer(reg, table, allow_write=False)
    reader = io.BytesIO(
        p.encode_message(_req(1, "initialize", {}))
        + p.encode_message(_req(2, "tools/list"))
    )
    writer = io.BytesIO()
    srv.serve(StdioTransport(reader, writer))
    lines = writer.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["id"] == 1 and json.loads(lines[1])["id"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_server.py -v`
Expected: FAIL (`ModuleNotFoundError: dunders.mcp.server`)

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/server.py
"""McpServer — JSON-RPC dispatch over a Transport. Never crashes on a bad call:
every exception maps to a JSON-RPC error response.
"""

from __future__ import annotations

import json
from importlib import metadata

from dunders.core.vfs import VfsRegistry
from dunders.mcp import errors, protocol
from dunders.mcp.mounts import MountTable
from dunders.mcp.tools import call_tool, tool_specs
from dunders.mcp.transport import Transport

__all__ = ["McpServer"]


def _version() -> str:
    try:
        return metadata.version("dunders")
    except metadata.PackageNotFoundError:
        return "0.0.0"


class McpServer:
    def __init__(self, registry: VfsRegistry, table: MountTable, *, allow_write: bool) -> None:
        self._registry = registry
        self._table = table
        self._allow_write = allow_write

    def serve(self, transport: Transport) -> None:
        while True:
            msg = transport.read_message()
            if msg is None:
                return  # EOF
            resp = self.handle(msg)
            if resp is not None:
                transport.write_message(resp)

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                return protocol.make_response(msg_id, {
                    "protocolVersion": protocol.PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dunders", "version": _version()},
                })
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return protocol.make_response(
                    msg_id, {"tools": tool_specs(self._allow_write)})
            if method == "tools/call":
                result = call_tool(
                    params.get("name", ""), params.get("arguments") or {},
                    table=self._table, registry=self._registry,
                    allow_write=self._allow_write,
                )
                return protocol.make_response(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(result)}]})
            return protocol.make_error(
                msg_id, protocol.METHOD_NOT_FOUND, f"unknown method {method!r}")
        except errors.McpError as exc:
            return protocol.make_error(msg_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001 — server must never crash
            m = errors.map_exception(exc)
            return protocol.make_error(msg_id, m.code, m.message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_server.py -v` → PASS
Run: `ruff check dunders/mcp/server.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/server.py tests/mcp/test_server.py
git commit -m "feat(mcp): McpServer dispatch (initialize/tools.list/tools.call, never-crash)"
```

---

### Task 9: Public entry `run_stdio` (`__init__.py`)

**Files:**
- Modify: `dunders/mcp/__init__.py`
- Test: `tests/mcp/test_run_stdio.py`

**Interfaces:**
- Produces: `run_stdio(registry, table, *, allow_write) -> None` — construct `McpServer` and serve `StdioTransport.stdio()`. Also re-export `McpServer`, `MountTable` for convenience.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_run_stdio.py
import io
import json
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
import dunders.mcp as mcp
from dunders.mcp import protocol as p
from dunders.mcp.mounts import MountTable
from dunders.mcp.transport import StdioTransport


def test_run_stdio_serves_over_given_transport(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": [
        {"label": "root", "uri": (tmp_path / "data").as_uri()}]}), encoding="utf-8")
    reg = VfsRegistry(); reg.register(LocalProvider())
    table = MountTable(reg, path=f)

    reader = io.BytesIO(p.encode_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    writer = io.BytesIO()
    monkeypatch.setattr(StdioTransport, "stdio",
                        classmethod(lambda cls: StdioTransport(reader, writer)))
    mcp.run_stdio(reg, table, allow_write=False)
    assert json.loads(writer.getvalue())["result"]["serverInfo"]["name"] == "dunders"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_run_stdio.py -v`
Expected: FAIL (`AttributeError: module 'dunders.mcp' has no attribute 'run_stdio'`)

- [ ] **Step 3: Write minimal implementation**

```python
# dunders/mcp/__init__.py
"""dunders MCP — expose the VFS to an external agent over the Model Context
Protocol (stdlib-only JSON-RPC). Public entry: run_stdio.
"""

from __future__ import annotations

from dunders.mcp.mounts import MountTable
from dunders.mcp.server import McpServer
from dunders.mcp.transport import StdioTransport

__all__ = ["run_stdio", "McpServer", "MountTable"]


def run_stdio(registry, table, *, allow_write: bool) -> None:
    """Serve MCP over stdio until stdin EOF."""
    McpServer(registry, table, allow_write=allow_write).serve(StdioTransport.stdio())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_run_stdio.py -v` → PASS
Run: `ruff check dunders/mcp/__init__.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/mcp/__init__.py tests/mcp/test_run_stdio.py
git commit -m "feat(mcp): run_stdio public entry point"
```

---

### Task 10: CLI wiring — `--mcp` headless branch (`main.py`)

**Files:**
- Modify: `dunders/main.py`
- Test: `tests/mcp/test_main.py`

**Interfaces:**
- Consumes: `dunders.mcp.run_stdio`, `dunders.fm.vfs_local.default_registry`, `dunders.mcp.mounts.MountTable`.
- Produces:
  - New argparse flags on the `dunders` parser: `--mcp` (store_true), `--mcp-write` (store_true), `--mcp-mounts` (comma string → `set`), `--mcp-bookmarks` (path string).
  - `def _run_mcp(args) -> None` — build `default_registry()`, `MountTable(registry, path=args.mcp_bookmarks or None, allow=<parsed set or None>)`, call `run_stdio(..., allow_write=args.mcp_write)`.
  - `main()` calls `_run_mcp(args)` and returns **before** constructing `DundersApp` when `args.mcp` is set.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_main.py
import dunders.main as m


def test_flags_parse():
    args = m._parse_args(["--mcp", "--mcp-write",
                          "--mcp-mounts", "a,b", "--mcp-bookmarks", "/tmp/bm.json"])
    assert args.mcp and args.mcp_write
    assert args.mcp_mounts == "a,b" and args.mcp_bookmarks == "/tmp/bm.json"


def test_main_mcp_branch_runs_server_not_tui(monkeypatch):
    called = {}
    monkeypatch.setattr(m, "_run_mcp", lambda args: called.setdefault("ran", True))
    # DundersApp must never be constructed in --mcp mode
    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("TUI started in --mcp mode")
    monkeypatch.setattr(m, "DundersApp", Boom)
    monkeypatch.setattr(m.sys, "argv", ["dunders", "--mcp"])
    m.main()
    assert called.get("ran") is True


def test_run_mcp_builds_table_and_serves(monkeypatch, tmp_path):
    import json
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": []}), encoding="utf-8")
    seen = {}

    def fake_run_stdio(registry, table, *, allow_write):
        seen["allow_write"] = allow_write
        seen["labels"] = {mt.label for mt in table.mounts()}
    monkeypatch.setattr(m, "run_stdio", fake_run_stdio)
    args = m._parse_args(["--mcp", "--mcp-bookmarks", str(f)])
    m._run_mcp(args)
    assert seen["allow_write"] is False and seen["labels"] == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_main.py -v`
Expected: FAIL (`AttributeError: _parse_args` has no `mcp` / no `_run_mcp` / no `run_stdio`)

- [ ] **Step 3: Write minimal implementation**

In `dunders/main.py`: add imports at top, extend `_parse_args`, add `_run_mcp`, branch in `main()`.

```python
# add near the top imports
from dunders.mcp import run_stdio
```

Add to `_parse_args` (inside the `dunders` parser, before `return`):

```python
    parser.add_argument("--mcp", action="store_true",
                        help="Run as a headless MCP server over stdio (no TUI).")
    parser.add_argument("--mcp-write", action="store_true",
                        help="Enable write tools (write_file/mkdir/delete/copy).")
    parser.add_argument("--mcp-mounts", default=None,
                        help="Comma-separated bookmark labels to expose (default: all).")
    parser.add_argument("--mcp-bookmarks", default=None,
                        help="Path to a bookmarks file to serve (default: config dir).")
```

Add the runner and branch:

```python
def _run_mcp(args: argparse.Namespace) -> None:
    from dunders.fm.vfs_local import default_registry
    from dunders.mcp.mounts import MountTable

    registry = default_registry()
    allow = (
        {s for s in (part.strip() for part in args.mcp_mounts.split(",")) if s}
        if args.mcp_mounts else None
    )
    table = MountTable(registry, path=args.mcp_bookmarks or None, allow=allow)
    run_stdio(registry, table, allow_write=args.mcp_write)


def main() -> None:
    args = _parse_args(sys.argv[1:])
    if args.mcp:
        _run_mcp(args)
        return
    launch_mode, initial_path = _resolve_launch_mode(args)
    DundersApp(launch_mode=launch_mode, initial_path=initial_path).run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/mcp/test_main.py -v` → PASS
Run: `pytest tests/mcp/ -q` → Expected: all MCP tests PASS
Run: `ruff check dunders/main.py` → clean

- [ ] **Step 5: Commit**

```bash
git add dunders/main.py tests/mcp/test_main.py
git commit -m "feat(mcp): dunders --mcp headless launch (write/mounts/bookmarks flags)"
```

---

### Task 11: End-to-end smoke + docs

**Files:**
- Test: `tests/mcp/test_e2e.py`
- Modify: `CLAUDE.md` (add a short `dunders.mcp` section under the architecture layers).

**Interfaces:**
- Consumes: everything. A full round-trip: initialize → tools/list → list_mounts → list_dir → read_file → grep over a real `LocalProvider` mount and a byte-stream transport.

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_e2e.py
import io
import json
from dunders.core.vfs import VfsRegistry
from dunders.fm.vfs_local import LocalProvider
from dunders.mcp import protocol as p
from dunders.mcp.mounts import MountTable
from dunders.mcp.server import McpServer
from dunders.mcp.transport import StdioTransport


def _call(mid, name, arguments):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def test_full_session(tmp_path):
    d = tmp_path / "data"; (d / "sub").mkdir(parents=True)
    (d / "sub" / "note.txt").write_text("find the needle\n")
    f = tmp_path / "bm.json"
    f.write_text(json.dumps({"bookmarks": [
        {"label": "root", "uri": d.as_uri()}]}), encoding="utf-8")
    reg = VfsRegistry(); reg.register(LocalProvider())
    srv = McpServer(reg, MountTable(reg, path=f), allow_write=False)

    stream = b"".join(p.encode_message(m) for m in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        _call(3, "list_mounts", {}),
        _call(4, "grep", {"mount": "root", "path": "", "pattern": "needle"}),
    ])
    writer = io.BytesIO()
    srv.serve(StdioTransport(io.BytesIO(stream), writer))
    out = [json.loads(x) for x in writer.getvalue().splitlines()]
    assert out[0]["result"]["serverInfo"]["name"] == "dunders"
    assert any(t["name"] == "grep" for t in out[1]["result"]["tools"])
    mounts = json.loads(out[2]["result"]["content"][0]["text"])["mounts"]
    assert mounts[0]["label"] == "root"
    grep = json.loads(out[3]["result"]["content"][0]["text"])
    assert grep["matches"][0]["path"] == "sub/note.txt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/mcp/test_e2e.py -v`
Expected: initially FAIL only if a prior task is incomplete; if all prior tasks landed it may pass immediately — that's acceptable for an integration smoke. If it passes, still complete steps 3–5 (docs).

- [ ] **Step 3: Add the docs section**

In `CLAUDE.md`, after the `dunders.forms` architecture section, add:

```markdown
### 2.7 `dunders.mcp` — MCP server over the VFS

App-agnostic, stdlib-only (like `dunders.ai`). Exposes the VFS to an external
LLM agent over the Model Context Protocol. `dunders --mcp` runs headless (no
TUI); the agent (Claude Desktop/Code) spawns it and talks JSON-RPC over stdio.
Bookmarks become mount points via a dynamic `MountTable` (re-reads
`bookmarks.json` on mtime change → live reconfiguration, no restart;
`--mcp-bookmarks PATH` serves a curated file). Tools: read
(`list_mounts`/`list_dir`/`read_file`/`stat`/`search`/`grep`) always; write
(`write_file`/`mkdir`/`delete`/`copy`) only behind `--mcp-write` (absent from
`tools/list` otherwise). Security: path-traversal guard, per-bookmark `"mcp":
false` opt-out, `--mcp-mounts` allowlist, no-AI-zone hiding for local mounts,
passwords never serialized. `protocol.py`/`transport.py`/`errors.py` are the
shared layer a future MCP *client* provider (`mcp://` in the panel) will reuse.
Spec: `docs/superpowers/specs/2026-07-28-mcp-server-design.md`.
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/mcp/ -q` → Expected: ALL PASS
Run: `pytest -q` → Expected: no regressions
Run: `ruff check dunders/mcp/ dunders/main.py dunders/config/bookmarks.py` → clean

- [ ] **Step 5: Commit**

```bash
git add tests/mcp/test_e2e.py CLAUDE.md
git commit -m "test(mcp): end-to-end session smoke + architecture docs"
```

---

## Self-Review

**Spec coverage:**
- Topology (dunders server, external agent, headless `--mcp`) → Task 10. ✓
- Transport abstracted, stdio now → Tasks 2, 9. ✓
- Tools-only primitive, `list_mounts` discovery → Task 6. ✓
- Read tools list_mounts/list_dir/read_file/stat/search/grep → Task 6. ✓
- Write tools behind `--mcp-write`, absent from `tools/list` → Tasks 7, 8, 10. ✓
- Stdlib-only, `dunders.mcp` package, shared protocol/transport/errors → Tasks 1–3. ✓
- Dynamic MountTable (mtime reload, cache prune) → Task 5. ✓
- `--mcp-bookmarks PATH` + `list_bookmarks(path)`/`bookmarks_mtime` → Tasks 4, 5, 10. ✓
- Security: traversal guard (Task 5), allowlist + `"mcp": false` (Task 5), no-AI hiding (Task 5), secrets never serialized (Task 3 map_exception + list_mounts fields Task 6), read cap / grep caps (Task 6) → ✓
- Lifecycle: handshake, framing stdout-only, EOF exit, never-crash → Tasks 2, 8. ✓
- Error codes -32001/-32002/-32003/-32010 + JSON-RPC standard codes → Tasks 1, 3, 8. ✓
- Testing plan (protocol/transport/mounts/tools/server/main + e2e) → Tasks 1–11. ✓

**Placeholder scan:** No TBD/TODO; every code step has concrete content. The only forward reference is `tools.py` → `tools_write.py` (Task 6 note explains the deferred import is unused until Task 7; read tests pass `allow_write=False`). ✓

**Type consistency:** `MountTable(registry, *, path, allow, noai_globs)`, `Mount(label, loc, scheme, display, password)`, `table.get/resolve/mounts/connected/ensure_connected`, `call_tool(name, args, *, table, registry, allow_write)`, `tool_specs(allow_write)`, `McpServer(registry, table, *, allow_write)`, `run_stdio(registry, table, *, allow_write)`, `make_error(id, code, message, data)`, `map_exception(exc)->McpError` — names/signatures identical across tasks. ✓

**Known scope notes (from spec, intentional):** no-AI hiding applies to `file`-scheme mounts only (no local marker for remote schemes); `stat` derives metadata by scanning the parent (no universal provider `stat`); `copy` destination is a directory keeping the source basename (no rename in v1 copy tool).
