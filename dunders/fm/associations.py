"""File-association table: parse, built-in defaults, and resolution (pure)."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass

# ext -> {verb: handler}. The defaults that make common types open correctly
# out of the box (and fix the .jpg-on-Enter crash). Verbs absent here resolve
# to the "auto" handler (current smart routing).
_IMAGE = {"open": "image", "view": "image"}
BUILTIN_DEFAULTS: dict[str, dict[str, str]] = {
    "jpg": dict(_IMAGE), "jpeg": dict(_IMAGE), "png": dict(_IMAGE),
    "gif": dict(_IMAGE), "bmp": dict(_IMAGE), "webp": dict(_IMAGE),
    "tiff": dict(_IMAGE), "tif": dict(_IMAGE), "ico": dict(_IMAGE),
    "csv": {"view": "csv"}, "tsv": {"view": "csv"},
    "md": {"open": "markdown", "view": "markdown"},
    "markdown": {"open": "markdown", "view": "markdown"},
    "pdf": {"view": "office"}, "docx": {"view": "office"},
    "pptx": {"view": "office"}, "xlsx": {"view": "office"},
    "epub": {"view": "office"},
}


@dataclass(frozen=True)
class BuiltinAction:
    handler: str


@dataclass(frozen=True)
class ExternalAction:
    command: str


Action = BuiltinAction | ExternalAction


def current_os_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def parse_associations(text: str) -> dict[str, dict[str, object]]:
    """Parse a TOML associations document. Non-table top-level keys are
    ignored. Raises ``tomllib.TOMLDecodeError`` on malformed input."""
    raw = tomllib.loads(text)
    out: dict[str, dict[str, object]] = {}
    for ext, verbs in raw.items():
        if not isinstance(verbs, dict):
            continue
        out[ext.lower()] = {str(k): v for k, v in verbs.items()}
    return out


def merge_tables(base: dict, user: dict) -> dict:
    """Merge ``user`` over ``base`` at (ext, verb) granularity. Pure."""
    out: dict[str, dict[str, object]] = {
        ext: dict(verbs) for ext, verbs in base.items()
    }
    for ext, verbs in user.items():
        dst = out.setdefault(ext, {})
        for verb, val in verbs.items():
            dst[verb] = val
    return out


def resolve(table: dict, ext: str, verb: str, os_name: str) -> Action:
    spec = table.get(ext.lower(), {})
    val = spec.get(verb)
    if isinstance(val, dict):
        val = val.get(os_name) or val.get("default")
    if isinstance(val, str):
        if val.startswith("!"):
            return ExternalAction(val[1:].strip())
        return BuiltinAction(val)
    return BuiltinAction("auto")
