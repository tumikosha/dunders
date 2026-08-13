"""`dunders --setup-claude` — wire this install into Claude Code.

The plugin cannot do this job on a fresh machine: `/plugin install` runs no
code, and its `SessionStart` hook fires too late and too briefly to install
anything. A `uv tool install` already put the editor on PATH, so the editor
itself is the natural place to finish the wiring — one command, no clone, no
marketplace, no shell profile:

    uv tool install "dunders[all] @ git+https://github.com/tumikosha/dunders"
    dunders --setup-claude

What it writes, all under ``~/.claude``:

* ``dunders-cc/cc-edit`` and ``dunders-cc/cc-session-map`` — copies of the
  wrappers bundled in the wheel. Copies rather than links into the tool's
  own directory, because that path carries the version and vanishes on the
  next upgrade, and a stale EDITOR pointing at a missing file leaves the user
  with no editor at all.
* ``settings.json`` — ``env.EDITOR`` pointing at that copy, plus a
  ``SessionStart`` hook running ``cc-session-map`` (the plugin declares that
  hook itself; here nobody else will).
* ``dunders-cc/installed.json`` — what was written, so removal is exact.

Stdlib only, no bash: everything here runs the same on any platform. The
wrappers themselves are bash, so on Windows the command reports that rather
than pretending to have succeeded.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

_HOOK_MARK = "cc-session-map"


def _scripts_dir() -> Path | None:
    """Where the bundled wrappers live.

    Installed wheel: ``dunders/claude_scripts/`` (see the force-include in
    pyproject). Editable install from a clone: the canonical ``skills/`` copy,
    so a developer gets the file they are actually editing.
    """
    packaged = Path(__file__).resolve().parent / "claude_scripts"
    if (packaged / "cc-edit").is_file():
        return packaged
    repo = Path(__file__).resolve().parent.parent / "skills" / "setup" / "scripts"
    if (repo / "cc-edit").is_file():
        return repo
    return None


def _write_json(path: Path, data: dict) -> None:
    """Atomic-ish write with a one-shot backup of what was there before."""
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak-dunders-cc"))
    tmp = path.with_suffix(path.suffix + ".dunders-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_settings(path: Path) -> dict | None:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _session_hook_present(settings: dict, command: str) -> bool:
    for entry in settings.get("hooks", {}).get("SessionStart", []):
        for hook in entry.get("hooks", []):
            if _HOOK_MARK in str(hook.get("command", "")):
                return True
    return False


def _add_session_hook(settings: dict, command: str) -> None:
    hooks = settings.setdefault("hooks", {})
    starts = hooks.setdefault("SessionStart", [])
    starts.append({
        "hooks": [{
            "type": "command",
            "command": command,
            "timeout": 5,
        }]
    })


def _drop_session_hook(settings: dict) -> None:
    starts = settings.get("hooks", {}).get("SessionStart")
    if not isinstance(starts, list):
        return
    kept = []
    for entry in starts:
        inner = [
            h for h in entry.get("hooks", [])
            if _HOOK_MARK not in str(h.get("command", ""))
        ]
        if inner:
            kept.append({**entry, "hooks": inner})
    if kept:
        settings["hooks"]["SessionStart"] = kept
    else:
        settings["hooks"].pop("SessionStart", None)
        if not settings["hooks"]:
            settings.pop("hooks", None)


def setup_claude(home: Path | None = None) -> tuple[int, list[str]]:
    """Install the integration. Returns ``(exit_code, lines_to_print)``."""
    home = home or Path.home()
    out: list[str] = []

    if os.name == "nt":
        return 1, [
            "The Claude Code wrappers are bash scripts, so this integration is",
            "macOS and Linux only for now. dunders itself runs fine on Windows —",
            "`__` works, only ctrl+x ctrl+e does not.",
        ]

    src = _scripts_dir()
    if src is None:
        return 1, [
            "Bundled wrappers not found. Reinstall with:",
            '  uv tool install --force "dunders[all] @ '
            'git+https://github.com/tumikosha/dunders.git"',
        ]

    target = home / ".claude" / "dunders-cc"
    settings_path = home / ".claude" / "settings.json"
    try:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("cc-edit", "cc-session-map"):
            shutil.copy2(src / name, target / name)
            (target / name).chmod(0o755)
    except OSError as exc:
        return 1, [f"Could not place the wrappers in {target}: {exc}"]
    out.append(f"wrappers  -> {target}")

    settings = _load_settings(settings_path)
    if settings is None:
        return 1, [
            f"{settings_path} is not readable JSON — left untouched.",
            "Fix or move it, then run `dunders --setup-claude` again.",
        ]

    shim = str(target / "cc-edit")
    env = settings.setdefault("env", {})
    current = env.get("EDITOR", "")
    if current and current != shim:
        # Someone else's editor stays. Saying so beats silently taking over a
        # setting the user chose.
        out.append(f"EDITOR    -> left alone (already {current!r})")
    else:
        env["EDITOR"] = shim
        out.append(f"EDITOR    -> {shim}")

    hook_cmd = str(target / "cc-session-map")
    if _session_hook_present(settings, hook_cmd):
        out.append("hook      -> already registered")
    else:
        _add_session_hook(settings, hook_cmd)
        out.append(f"hook      -> SessionStart {hook_cmd}")

    try:
        _write_json(settings_path, settings)
    except OSError as exc:
        return 1, [f"Could not write {settings_path}: {exc}"]

    state = {
        "editor": shim,
        "hook": hook_cmd,
        "managed_by": "dunders --setup-claude",
    }
    try:
        (target / "installed.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass

    out.append("")
    out.append("Done. Start a new claude session, then press Ctrl+G (or")
    out.append("ctrl+x ctrl+e) — the prompt opens in __ with the transcript below.")
    out.append("Remove it again with: dunders --remove-claude")
    return 0, out


def remove_claude(home: Path | None = None) -> tuple[int, list[str]]:
    """Undo `--setup-claude`: settings entry, hook, and the copied wrappers."""
    home = home or Path.home()
    out: list[str] = []
    target = home / ".claude" / "dunders-cc"
    settings_path = home / ".claude" / "settings.json"

    settings = _load_settings(settings_path)
    if settings is None:
        out.append(f"{settings_path} is not readable JSON — left untouched.")
    else:
        shim = str(target / "cc-edit")
        env = settings.get("env", {})
        # Only drop EDITOR if it is still ours; the user may have re-pointed it.
        if env.get("EDITOR") == shim:
            env.pop("EDITOR", None)
            if not env:
                settings.pop("env", None)
            out.append("EDITOR    -> removed")
        elif env.get("EDITOR"):
            out.append(f"EDITOR    -> left alone ({env['EDITOR']!r} is not ours)")
        _drop_session_hook(settings)
        out.append("hook      -> removed")
        try:
            _write_json(settings_path, settings)
        except OSError as exc:
            out.append(f"could not write {settings_path}: {exc}")

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
        out.append(f"wrappers  -> deleted {target}")
    shutil.rmtree(home / ".claude" / "session-map", ignore_errors=True)

    out.append("")
    out.append("dunders itself is still installed — `__` keeps working.")
    out.append("Remove that too with: uv tool uninstall dunders")
    return 0, out


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `--setup-claude` / `--remove-claude` flags."""
    argv = sys.argv[1:] if argv is None else argv
    code, lines = (
        remove_claude() if "--remove-claude" in argv else setup_claude()
    )
    for line in lines:
        print(line)
    return code
