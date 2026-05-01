"""tyui entry point — selects launch mode from argv."""

from __future__ import annotations

import argparse
import os
import sys

from tyui.app import TyuiApp


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tyui",
        description=(
            "tyui — terminal shell with NC-style file panels, embedded "
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
    TyuiApp(launch_mode=launch_mode, initial_path=initial_path).run()


if __name__ == "__main__":
    main()
