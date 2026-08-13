"""dunders entry point — selects launch mode from argv."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dunders.app import DundersApp
from dunders.mcp import run_stdio


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dunders",
        description=(
            "dunders — terminal shell with NC-style file panels, embedded "
            "editor, and agent CLI mode."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional file or directory. Files open in the editor; "
             "directories open both panels at that path.",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Start in agent / CLI mode instead of the file manager.",
    )
    parser.add_argument(
        "--pd",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Directory the Project View tree opens at. Defaults to the "
             "current working directory; pass an empty value (--pd '') to "
             "use the edited file's own directory instead.",
    )
    parser.add_argument("--mcp", action="store_true",
                        help="Run as a headless MCP server over stdio (no TUI).")
    parser.add_argument("--mcp-write", action="store_true",
                        help="Enable write tools (write_file/mkdir/delete/copy).")
    parser.add_argument("--mcp-mounts", default=None,
                        help="Comma-separated bookmark labels to expose (default: all).")
    parser.add_argument("--mcp-bookmarks", default=None,
                        help="Path to a bookmarks file to serve (default: config dir).")
    return parser.parse_args(argv)


def _resolve_launch_mode(args: argparse.Namespace) -> tuple[str, str | None]:
    """Return (launch_mode, initial_path) given parsed args."""
    if args.cli:
        return ("cli", args.path)  # path optional, used to seed panel cwd
    if args.path is None:
        return ("fm", None)
    if os.path.isfile(args.path):
        return ("editor", args.path)
    # treat anything else (existing dir, missing path) as fm-mode initial cwd
    return ("fm", args.path)


def _resolve_project_dir(
    pd: str | None, launch_mode: str, file_path: str | None
) -> str | None:
    """Resolve ``--pd`` into the directory the Project View tree opens at.

    - ``--pd PATH``     -> that directory, whatever the launch mode. A PATH
                           naming a file resolves to the file's directory,
                           since a tree can only be rooted at a directory.
    - ``--pd`` / ``''`` -> the edited file's own directory (the pre-flag
                           behaviour), or the cwd when nothing is being edited.
    - flag absent       -> the process cwd for an editor launch, ``None`` for a
                           file-manager launch (which keeps seeding its panels
                           from the positional path).

    The cwd default is the point of the flag: ``__ FILE`` is normally run from
    the project root, and the tree is far more useful there than in whatever
    directory the file happens to live in.
    """
    if pd is not None and pd != "":
        given = Path(pd).expanduser()
        if given.is_file():
            return str(given.resolve().parent)
        return pd
    editor_launch = launch_mode in ("editor", "we")
    if pd == "":
        if file_path is not None:
            # Absolute: the argument is usually relative to the cwd, and the
            # panel should not have to re-resolve it later.
            return str(Path(file_path).expanduser().resolve().parent)
        return str(Path.cwd())
    return str(Path.cwd()) if editor_launch else None


def _reclaim_pd_file(pd: str | None, paths: list[str]) -> tuple[str | None, list[str]]:
    """Undo argparse eating the positional path as ``--pd``'s value.

    ``--pd`` takes an optional value, so ``__ --pd file.md`` parses as
    ``pd="file.md"`` with no positional path at all — the file silently never
    opens. When the value names an existing file and nothing else was given,
    it was meant as the file to edit: hand it back to the positional list and
    treat the flag as bare (root the tree at that file's directory).
    """
    if pd and not paths and Path(pd).expanduser().is_file():
        return ("", [pd])
    return (pd, paths)


def _run_mcp(args: argparse.Namespace) -> None:
    from dunders.fm.vfs_local import default_registry
    from dunders.mcp.mounts import MountTable

    registry = default_registry()
    allow = (
        {s for s in (part.strip() for part in args.mcp_mounts.split(",")) if s}
        if args.mcp_mounts else None
    )
    path = Path(args.mcp_bookmarks) if args.mcp_bookmarks else None
    table = MountTable(registry, path=path, allow=allow)
    run_stdio(registry, table, allow_write=args.mcp_write)


def main() -> None:
    args = _parse_args(sys.argv[1:])
    if args.mcp:
        _run_mcp(args)
        return
    args.pd, reclaimed = _reclaim_pd_file(args.pd, [args.path] if args.path else [])
    args.path = reclaimed[0] if reclaimed else None
    launch_mode, initial_path = _resolve_launch_mode(args)
    project_dir = _resolve_project_dir(
        args.pd, launch_mode, initial_path if launch_mode == "editor" else None
    )
    DundersApp(
        launch_mode=launch_mode,
        initial_path=initial_path,
        project_dir=project_dir,
    ).run()


def _resolve_we_args(
    argv: list[str],
) -> tuple[str, str | None, list[str], str, str | None]:
    """Resolve the `we` command line.

    Returns ``(launch_mode, initial_path, file_paths, terminal_mode,
    project_dir)``.

    - no positional paths            -> ("we-mc", None, [], <mode>)
    - only a directory               -> ("we-mc", <dir>, [], <mode>)
    - one or more real files         -> ("we", None, <files>, <mode>)

    ``terminal_mode`` is "suspend" when ``--suspend`` is given, else "relay".
    ``project_dir`` follows ``--pd`` (see `_resolve_project_dir`).
    """
    parser = argparse.ArgumentParser(
        prog="we",
        description="we — Midnight-Commander-style file manager / editor.",
    )
    parser.add_argument(
        "--suspend",
        action="store_true",
        help="Run shell commands via suspend+subprocess instead of a "
        "persistent relay subshell (cross-platform, no persistent session).",
    )
    parser.add_argument(
        "--pd",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="Directory the Project View tree opens at. Defaults to the "
        "current working directory; pass an empty value (--pd '') to use the "
        "edited file's own directory instead.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="Files open in cascaded editor windows; a lone directory or no "
        "args open the mc-style file manager.",
    )
    ns = parser.parse_args(argv)
    terminal_mode = "suspend" if ns.suspend else "relay"
    pd, paths = _reclaim_pd_file(ns.pd, ns.paths)

    def _pd(mode: str, first_file: str | None) -> str | None:
        return _resolve_project_dir(pd, mode, first_file)

    if not paths:
        return ("we-mc", None, [], terminal_mode, _pd("we-mc", None))
    file_paths = [p for p in paths if not os.path.isdir(p)]
    if not file_paths:
        return ("we-mc", paths[0], [], terminal_mode, _pd("we-mc", None))
    return ("we", None, file_paths, terminal_mode, _pd("we", file_paths[0]))


def main_we() -> None:
    launch_mode, initial_path, file_paths, terminal_mode, project_dir = (
        _resolve_we_args(sys.argv[1:])
    )
    DundersApp(
        launch_mode=launch_mode,
        initial_path=initial_path,
        initial_paths=file_paths,
        terminal_mode=terminal_mode,
        project_dir=project_dir,
    ).run()


def main_wew() -> None:
    """`__w` == `we --suspend` — suspend mode, primary launcher on Windows."""
    launch_mode, initial_path, file_paths, terminal_mode, project_dir = (
        _resolve_we_args(["--suspend", *sys.argv[1:]])
    )
    DundersApp(
        launch_mode=launch_mode,
        initial_path=initial_path,
        initial_paths=file_paths,
        terminal_mode=terminal_mode,
        project_dir=project_dir,
    ).run()


if __name__ == "__main__":
    main()
