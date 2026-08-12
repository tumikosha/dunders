# MCP server over the dunders VFS — design

**Date:** 2026-07-28
**Status:** approved (brainstorm), pre-plan

## Summary

Expose the dunders virtual filesystem — every backend the panel already speaks
(local, sftp, ftp, zip/7z, docker, db, gdrive) — to an external LLM agent over
the **Model Context Protocol (MCP)**. The agent connects to `dunders --mcp`
(headless, no TUI) and browses/reads (optionally writes) the resources saved as
**bookmarks**, each becoming a *mount point* in a single unified tree. One
protocol reaches sftp + gdrive + a SQLite table + a zip member — something a
plain filesystem-MCP cannot do.

This spec covers **the server side plus the shared protocol layer**. A symmetric
future direction — dunders as an MCP *client*, mounting other people's MCP
servers into the panel (`mcp://` scheme, their Resources → files) — is a separate
spec, but the shared layer here is designed up front to serve both sides.

## Decisions (from brainstorm)

- **Topology:** dunders is the **server**; an external agent (Claude
  Desktop/Code / any MCP client) is the client. `dunders --mcp` runs headless.
- **Transport:** abstracted behind a `Transport` protocol. **stdio now**
  (line-delimited JSON over stdin/stdout); HTTP/SSE later via the same interface.
- **Operations:** read **and** write, but write is **behind a flag**. v1 defaults
  to **read-only**; `--mcp-write` enables the write tools.
- **MCP primitive:** **Tools** (not Resources) — lazy, scales to multi-GB sftp
  and huge db tables; discovery via a `list_mounts` tool.
- **Implementation:** **stdlib-only**, hand-rolled JSON-RPC 2.0. New package
  `dunders.mcp`, zero dependencies — matching the `dunders.ai` DNA (which speaks
  REST/SSE over bare `urllib`). No `mcp` SDK, no pydantic/anyio.

## Architecture

New **app-agnostic** package `dunders.mcp` (like `dunders.ai` / `windowing`):
imports only `dunders.core.vfs` and `dunders.config.bookmarks`; never imports
`fm` / `app`. The shared protocol/transport layer is factored out so both the
server (this spec) and the future client stand on it without duplicating framing.

```
dunders/mcp/
  __init__.py    public entry: run_stdio(registry, mounts, *, allow_write)
  protocol.py    JSON-RPC 2.0 framing, envelopes, MCP message types (SHARED)
  transport.py   Transport protocol; StdioTransport (+ HttpTransport later) (SHARED)
  errors.py      provider/JSON-RPC error mapping (SHARED)
  server.py      McpServer — dunders serves its VFS outward (THIS spec)
  mounts.py      MountTable — bookmarks → mount points (server side)
  tools.py       tool definitions (inputSchema) + handlers over VfsRegistry
  # client.py, fm/providers/mcp_provider.py — NEXT spec, not built here
```

- **`McpServer`** holds a `VfsRegistry`, a `MountTable`, and the `allow_write`
  flag. It reads frames from a `Transport`, dispatches `initialize` /
  `tools/list` / `tools/call`, writes responses. Transport-agnostic:
  `StdioTransport` now, `HttpTransport` plugs into the same interface later.
- **`MountTable`** builds `label → VfsPath.parse(uri)` (+ password) from a
  bookmarks file. The file path is **configurable** — it takes a `path` argument
  defaulting to `bookmarks_path()`, so the server can point at a dedicated,
  curated MCP-only bookmarks file (`--mcp-bookmarks PATH`) distinct from the TUI's
  own. It is also **dynamic**: it re-reads the file when its mtime changes, so
  mounts added/removed in the TUI (or any editor) appear **live, without a server
  restart** (see *Live reconfiguration*). Slow providers connect **lazily** on
  first access via `provider.resolve_target(spec, base, password)` — a dead sftp
  bookmark must not block startup. Addressing: the agent supplies `mount`
  (bookmark label) + a `/`-relative `path`; the server joins `root_loc` +
  `path.split('/')` into a `VfsPath`.
- **Isolation:** every module is unit-tested against a fake `Transport` and a
  fake registry, mirroring the `FakeTransport` / `FakeDrive` pattern from the
  gdrive tests.

### Why factor the shared layer now

The future MCP-*client* provider (`mcp://` scheme, someone else's server mounted
into the panel, their Resources → files like db tables → directories) needs the
exact same JSON-RPC framing, transports, message types, and error mapping.
Burying those in `server.py` would force a duplicate for the client. `server.py`
and the future `client.py` both stand on `protocol.py` + `transport.py`. Bonus /
noted: a bookmarked `mcp://` mount re-served by our own server is legal recursion.

## Tool surface

Addressing everywhere: `mount` = bookmark label, `path` = `/`-relative inside the
mount. The server computes `root_loc = MountTable[mount]`, descends
`path.split('/')` into a `VfsPath`. A `..` that escapes the mount root is
rejected (traversal guard) *before* any provider call. Empty / `.` = mount root.

Each tool ships a JSON Schema (`inputSchema`). Provider errors map to JSON-RPC
errors (see below).

### Read tools (always present)

- **`list_mounts()`** — the bookmarks as mount points: `label`, `scheme`,
  `display`, `connected?`. This is discovery (we chose Tools over Resources).
- **`list_dir(mount, path="")`** — one directory's entries: `name`, `is_dir`,
  `size`, `mtime`. Lazy, single folder (no recursion).
- **`read_file(mount, path, offset=0, length=null)`** — via `provider.open_read`.
  Text-decodable → `text`; else → `base64` + `binary` flag. Default cap **1 MiB**
  (never flood the context with a multi-GB sftp file); `offset`/`length` for
  ranged reads. Google-native docs export through the existing `open_read`.
- **`stat(mount, path)`** — one item's metadata.
- **`search(mount, path, glob, max=200)`** — recursive **name** search (glob).
- **`grep(mount, path, pattern, glob="*", ignore_case=false, max_matches=200,
  max_file_bytes=1MiB)`** — recursive **content** search: read files via
  `provider.open_read`, regex per line, return `[{path, line_no, line}]`. Skips
  binaries (NUL sniff) and files over `max_file_bytes`; caps at `max_matches`;
  `glob` pre-filters by name so a full remote walk isn't forced.

### Write tools (only when `allow_write`)

- **`write_file(mount, path, content|base64, overwrite=false)`**
- **`mkdir(mount, path)`**
- **`delete(mount, path)`**
- **`copy(src_mount, src_path, dst_mount, dst_path)`** — routes through the
  existing `transfer()` engine, so cross-scheme copy (sftp → gdrive) is free.

When `allow_write=False` these tools are **absent from `tools/list`** — not
"denied", they simply don't exist, so the agent never sees them.

## Live reconfiguration

The mount table is reconfigurable **on the fly, without restarting the server**.
The source of truth is the bookmarks file on disk (atomic, 0600) — not shared
memory or a thread — so a separate-process server and a running TUI stay in sync
through the file alone.

- `MountTable` keeps the file mtime + the parsed snapshot. On each `list_mounts`
  / `tools/call` it cheaply stats the file; if the mtime changed, it re-reads
  (the file is tiny). No polling thread, no fs-watch dependency.
- The TUI edits bookmarks as usual (`add_bookmark` / `remove_bookmark` already
  write atomically) → the server sees the new set on its next request. No restart.
- Lazy connections are cached per label. On reload: vanished or changed labels
  drop their cached connection; new labels appear and connect lazily on first
  access. A request against a just-removed mount → normal -32001.
- **Tool set is unchanged by reload.** `list_dir` / `grep` / … are fixed; only
  the *data* returned by `list_mounts` changes. So no `notifications/
  tools/list_changed` is needed — the agent simply calls `list_mounts` again and
  sees the fresh mounts.

This is why a **separate process (client spawns `dunders --mcp`)** is fully
sufficient: live reconfiguration flows through the disk, not shared memory, so
the in-TUI-thread approach (with its stdout/stdin contention against Textual and
`call_from_thread` marshaling) is not needed.

## Security

The far end is an autonomous LLM, and bookmarks hold plaintext passwords to
other people's sftp/DB; `copy` can move data between remote hosts. This is the
highest-risk surface.

**Access boundaries**
- **Path traversal:** every resolved `VfsPath` must stay a descendant of its
  mount root. A `..` above root, or an absolute path, is rejected with a
  JSON-RPC error *before* the provider is called. Empty / `.` = root.
- **Mount allowlist:** default serves **all** bookmarks. `--mcp-mounts a,b`
  narrows to a subset. Per-bookmark opt-out: a bookmark with `"mcp": false` is
  not mounted (extends the bookmark dict; default = mounted).
- **Curated bookmarks file:** `--mcp-bookmarks PATH` points the server at a
  dedicated file, so the agent can be given a hand-picked, safer subset that is
  entirely separate from the TUI's day-to-day bookmarks. Same 0600 /
  fault-tolerant read discipline as the default file.
- **Read-only by default:** write tools are absent from `tools/list` without
  `--mcp-write`. Not an in-tool check — they are not registered.

**Secrets**
- Bookmark passwords are used only for `resolve_target` on connect. They are
  **never** serialized into MCP responses (`list_mounts` returns `scheme` /
  `display`, never a credential). `display()` may contain `user@host` (host is
  not a secret; kept) but never the password field.
- No secrets in stderr logs. A connection failure returns a generic
  "mount X unavailable" — never a URI carrying a password.

**No-AI zones** — reuse `is_ai_allowed(path, cloud=…)` from the ai layer. A local
mount under `.dunders-noai` / `noai_globs` is hidden from `list_mounts` and all
access, symmetric with the existing cloud-redaction policy.

**Resource limits (DoS):** `read_file` 1 MiB cap; `grep`/`search` capped by
match count and per-file bytes; `list_dir` is one folder. A slow provider cannot
hang the server on an unbounded walk — cancel/limit checked at the item boundary,
as with copy.

**Explicit write consent:** `--mcp-write` in v1 is a global flag. Per-mount write
(`--mcp-write label1`) is noted as a v2 extension, not built now (YAGNI).

## Lifecycle & errors

**Launch.** `dunders --mcp` branches in `main.py` *before* `DundersApp().run()`
(headless, no TUI). It builds `default_registry()` + a `MountTable` over the
bookmarks file (`args.mcp_bookmarks` or the default `bookmarks_path()`) and calls
`dunders.mcp.run_stdio(registry, mounts, allow_write=args.mcp_write)`. Flags:
`--mcp`, `--mcp-write`, `--mcp-mounts a,b`, `--mcp-bookmarks PATH`.

**Handshake.** Client sends `initialize` → server replies `protocolVersion`,
`capabilities={tools:{}}`, `serverInfo={name:"dunders", version}`. Then
`tools/list` (set depends on `allow_write`), then `tools/call`.

**Framing.** `StdioTransport` reads line-delimited JSON from stdin, writes to
stdout. Logs/diagnostics go to **stderr only** — stdout is reserved for the
protocol, or a frame corrupts. EOF on stdin → clean exit.

**Errors** (`errors.py`, modeled on `_http.map_status` in ai):
- Parse / invalid JSON → -32700; unknown method → -32601; bad params → -32602
  (standard JSON-RPC).
- Domain: mount missing → -32001; path missing (`FileNotFoundError`) → -32002;
  traversal / access denied → -32003; write while `allow_write=False` → the
  method simply isn't there; provider died (`DriveError` / network) → -32010 with
  generic text (no secrets).
- Any unhandled exception inside a tool is caught by the dispatcher and mapped to
  an error response. **The server never crashes** on a single bad call — same
  discipline as "a provider scan must never crash the TUI".

**Cancel/limits.** A long `grep`/`copy` over a slow provider is bounded by the
caps above. v1 has no async-cancel mid-call (the client drops the connection on
timeout); cooperative cancel is noted as v2.

## Testing

All offline, using the `FakeTransport` / `FakeDrive` pattern from the gdrive
tests. Unit-first, stdlib-only.

- **`tests/mcp/test_protocol.py`** — framing as pure functions: encode/decode
  line-delimited, bad JSON → parse-error envelope, request/response/error
  round-trip. No I/O.
- **`tests/mcp/test_transport.py`** — `StdioTransport` over `io.BytesIO` / a pipe
  pair: write frames, read back, EOF → clean stop; stdout carries only protocol.
- **`tests/mcp/test_server.py`** — the core. Fake in-memory registry
  (LocalProvider on `tmp_path` + a fake slow provider):
  - handshake: `initialize` → capabilities/serverInfo; `tools/list` varies with
    `allow_write`.
  - each read tool: `list_mounts` / `list_dir` / `read_file` (text + binary +
    base64 + ranged + 1 MiB cap) / `stat` / `search` glob / `grep` (matches, case,
    max, NUL-skip, per-file cap).
  - write tools: present when `allow_write=True`, **absent** when False;
    `write_file` / `mkdir` / `delete` / `copy` (incl. cross-mount via `transfer()`).
  - **security**: traversal (`..` above root) → -32003; unknown mount → -32001;
    write without the flag → method absent; password NOT in `list_mounts`; no-AI
    mount hidden.
  - errors: unknown method → -32601, bad params → -32602, provider exception
    mapped, server stays alive.
- **`tests/mcp/test_mounts.py`** — `MountTable` from fake bookmarks: label→loc,
  `"mcp": false` skipped, `--mcp-mounts` narrows, lazy connect (no
  `resolve_target` before first access); **custom path** (`--mcp-bookmarks`)
  reads the given file, not the default; **reload on change** — rewrite the file
  (bump mtime), next `list_mounts` reflects add/remove, a removed slow mount's
  cached connection is dropped.
- **`tests/mcp/test_main.py`** — `--mcp` / `--mcp-write` / `--mcp-mounts` /
  `--mcp-bookmarks` parse, the branch reaches `run_stdio` (mocked), TUI does not
  start.
- `ruff check` clean.

**Done criterion:** an external MCP client (Claude Desktop) sees the bookmarks,
browses `list_dir` / `read_file` / `grep` across sftp + gdrive + local under one
protocol; write is invisible without `--mcp-write`; passwords never leak; all
tests green.

## Out of scope (v1)

- dunders as MCP *client* (`mcp://` provider mounting other servers) — next spec.
- HTTP/SSE transport (interface reserved, not implemented).
- Per-mount write flags; cooperative mid-call cancel; content-search caching.
- MCP Resources primitive (Tools-only chosen).
