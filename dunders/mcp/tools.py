"""MCP tool specs + handlers over the VfsRegistry. Read tools always present;
write tools appended only when allow_write (see WRITE_TOOLS in the write half).
"""

from __future__ import annotations

import base64
import fnmatch
import re

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
    cap = READ_CAP if length is None else min(max(0, int(length)), READ_CAP)
    with provider.open_read(loc) as fh:
        if offset:
            _skip(fh, offset)
        data = fh.read(cap)
        eof = not fh.read(1)
    if b"\x00" not in data:
        try:
            return {"encoding": "text", "data": data.decode("utf-8"), "eof": eof}
        except UnicodeDecodeError:
            pass
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
