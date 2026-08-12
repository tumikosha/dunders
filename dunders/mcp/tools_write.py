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
