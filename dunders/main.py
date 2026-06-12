"""dunders entry point — selects launch mode from argv."""

from __future__ import annotations

import argparse
import os
import sys

from dunders.app import DundersApp


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


def main() -> None:
    args = _parse_args(sys.argv[1:])
    launch_mode, initial_path = _resolve_launch_mode(args)
    DundersApp(launch_mode=launch_mode, initial_path=initial_path).run()


def _resolve_we_args(
    argv: list[str],
) -> tuple[str, str | None, list[str], str]:
    """Resolve the `we` command line.

    Returns ``(launch_mode, initial_path, file_paths, terminal_mode)``.

    - no positional paths            -> ("we-mc", None, [], <mode>)
    - only a directory               -> ("we-mc", <dir>, [], <mode>)
    - one or more real files         -> ("we", None, <files>, <mode>)

    ``terminal_mode`` is "suspend" when ``--suspend`` is given, else "relay".
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
        "paths",
        nargs="*",
        default=[],
        help="Files open in cascaded editor windows; a lone directory or no "
        "args open the mc-style file manager.",
    )
    ns = parser.parse_args(argv)
    terminal_mode = "suspend" if ns.suspend else "relay"
    paths = ns.paths

    if not paths:
        return ("we-mc", None, [], terminal_mode)
    file_paths = [p for p in paths if not os.path.isdir(p)]
    if not file_paths:
        return ("we-mc", paths[0], [], terminal_mode)
    return ("we", None, file_paths, terminal_mode)


def main_we() -> None:
    launch_mode, initial_path, file_paths, terminal_mode = _resolve_we_args(
        sys.argv[1:]
    )
    DundersApp(
        launch_mode=launch_mode,
        initial_path=initial_path,
        initial_paths=file_paths,
        terminal_mode=terminal_mode,
    ).run()


def main_wew() -> None:
    """`__w` == `we --suspend` — suspend mode, primary launcher on Windows."""
    launch_mode, initial_path, file_paths, terminal_mode = _resolve_we_args(
        ["--suspend", *sys.argv[1:]]
    )
    DundersApp(
        launch_mode=launch_mode,
        initial_path=initial_path,
        initial_paths=file_paths,
        terminal_mode=terminal_mode,
    ).run()


if __name__ == "__main__":
    main()
