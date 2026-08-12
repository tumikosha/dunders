#!/usr/bin/env bash
# Install dunders as Claude Code's external editor, with session history.
#
# Everything here is idempotent: rerunning replaces the managed block in the
# shell profile and rewrites the same files, so it is safe to run after an
# upgrade or a change of editor.
#
# Usage:
#   ./install.sh                     install everything
#   ./install.sh --editor nvim       wire up a different editor instead of __
#   ./install.sh --skip-dunders      only the wrapper (dunders already present)
#   ./install.sh --skip-hook         skip hook registration (the plugin ships it)
#   ./install.sh --check             report current state, change nothing
#   ./install.sh --uninstall         remove the wrapper, hook and profile block
#   ./install.sh --purge             --uninstall plus the log and dunders itself
#   ./install.sh --purge --yes       same, without the confirmation prompt
#
# Part of dunders — https://github.com/tumikosha/dunders

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.claude/dunders-cc"
SETTINGS="$HOME/.claude/settings.json"
BEGIN_MARK="# >>> dunders claude-code editor >>>"
END_MARK="# <<< dunders claude-code editor <<<"

EDITOR_CMD="__"
SKIP_DUNDERS=0
SKIP_HOOK=0
MODE="install"
PURGE=0
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --editor) EDITOR_CMD="${2:?--editor needs a value}"; shift 2 ;;
    --skip-dunders) SKIP_DUNDERS=1; shift ;;
    --skip-hook) SKIP_HOOK=1; shift ;;
    --check) MODE="check"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    --purge) MODE="uninstall"; PURGE=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    # Print the header comment block, however long it grows, and stop at code.
    -h|--help) awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' \
                 "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
head_() { printf '\n== %s\n' "$*"; }

profile_path() {
  case "${SHELL##*/}" in
    zsh)  printf '%s\n' "$HOME/.zshrc" ;;
    bash) [[ "$(uname)" == "Darwin" ]] && printf '%s\n' "$HOME/.bash_profile" \
                                       || printf '%s\n' "$HOME/.bashrc" ;;
    *)    printf '%s\n' "$HOME/.profile" ;;
  esac
}
PROFILE="$(profile_path)"

# --- check -------------------------------------------------------------------
if [[ "$MODE" == "check" ]]; then
  head_ "dunders"
  if command -v __ >/dev/null 2>&1; then say "__ -> $(command -v __)"; else say "__ NOT installed"; fi
  head_ "wrapper"
  [[ -x "$TARGET_DIR/cc-edit" ]] && say "cc-edit installed at $TARGET_DIR" || say "cc-edit NOT installed"
  head_ "hook"
  if [[ -f "$SETTINGS" ]] && grep -q 'cc-session-map' "$SETTINGS"; then
    say "SessionStart hook registered"
  else
    say "SessionStart hook NOT registered"
  fi
  head_ "profile ($PROFILE)"
  grep -q "$BEGIN_MARK" "$PROFILE" 2>/dev/null && say "managed block present" || say "managed block absent"
  head_ "live sessions in map"
  ls "$HOME/.claude/session-map"/*.json >/dev/null 2>&1 \
    && say "$(ls "$HOME/.claude/session-map"/*.json | wc -l | tr -d ' ') entries" \
    || say "none — start a new claude session so the hook can fire"
  exit 0
fi

# --- uninstall ---------------------------------------------------------------
if [[ "$MODE" == "uninstall" ]]; then
  # Purge reaches beyond this integration: it uninstalls dunders itself, which
  # the user may well be using outside Claude Code. Confirm before doing that.
  if [[ "$PURGE" == "1" && "$ASSUME_YES" == "0" ]]; then
    if [[ -t 0 ]]; then
      printf 'Purge removes the integration, %s, and dunders itself.\nContinue? [y/N] ' \
        "$HOME/.claude/cc-edit.log"
      read -r reply
      [[ "$reply" == [Yy] || "$reply" == [Yy][Ee][Ss] ]] || { echo "aborted"; exit 1; }
    else
      echo "--purge needs a terminal to confirm; pass --yes for unattended runs" >&2
      exit 1
    fi
  fi

  head_ "removing wrapper"
  rm -rf "$TARGET_DIR" && say "deleted $TARGET_DIR"

  head_ "removing hook from settings.json"
  if [[ -f "$SETTINGS" ]]; then
    cp "$SETTINGS" "$SETTINGS.bak-dunders-cc"
    python3 - "$SETTINGS" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
hooks = d.get("hooks", {})
entries = hooks.get("SessionStart", [])
kept = [e for e in entries if "cc-session-map" not in json.dumps(e)]
if kept:
    hooks["SessionStart"] = kept
else:
    hooks.pop("SessionStart", None)
if not hooks:
    d.pop("hooks", None)
json.dump(d, open(p, "w"), indent=2)
open(p, "a").write("\n")
PY
    say "hook removed (backup: $SETTINGS.bak-dunders-cc)"
  fi

  head_ "removing profile block"
  if grep -q "$BEGIN_MARK" "$PROFILE" 2>/dev/null; then
    cp "$PROFILE" "$PROFILE.bak-dunders-cc"
    awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
      index($0,b) { skip=1 } !skip { print } index($0,e) { skip=0 }
    ' "$PROFILE.bak-dunders-cc" > "$PROFILE"
    say "block removed from $PROFILE (backup: $PROFILE.bak-dunders-cc)"
  else
    say "no block found"
  fi

  rm -rf "$HOME/.claude/session-map"

  if [[ "$PURGE" == "1" ]]; then
    head_ "purging diagnostics"
    rm -f "$HOME/.claude/cc-edit.log" && say "deleted ~/.claude/cc-edit.log"

    head_ "purging dunders"
    # Both managers get checked, not just the first hit: dunders can be present
    # under uv *and* pipx at once, and removing only one leaves `__` on PATH
    # pointing at the survivor, which looks like the purge silently failed.
    removed=0
    if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^dunders'; then
      uv tool uninstall dunders && { say "uninstalled dunders (uv)"; removed=1; }
    fi
    if command -v pipx >/dev/null 2>&1 && pipx list 2>/dev/null | grep -q 'package dunders'; then
      pipx uninstall dunders && { say "uninstalled dunders (pipx)"; removed=1; }
    fi
    [[ "$removed" == "1" ]] || say "dunders not installed via uv or pipx — nothing to remove"
    if command -v __ >/dev/null 2>&1; then
      say "note: __ is still on PATH at $(command -v __) — installed some other way"
    fi
  fi

  printf '\nDone. Restart claude and open a new shell.\n'
  exit 0
fi

# --- what this gets you ------------------------------------------------------
# Printed before any work: the key is the whole point of the integration, and a
# user who reads only the first screen should still learn it.
cat <<'BANNER'

Ctrl+G — the whole loop, one key:
  in Claude Code   Ctrl+G opens your prompt in the editor, with the session
                   transcript below it
  in the editor    Ctrl+G saves and exits, sending only what you typed
BANNER

# --- prerequisites -----------------------------------------------------------
head_ "prerequisites"
command -v python3 >/dev/null 2>&1 || {
  echo "python3 is required (the wrapper parses the transcript with it)" >&2
  exit 1
}
say "python3 -> $(command -v python3)"

# --- dunders -----------------------------------------------------------------
if [[ "$SKIP_DUNDERS" == "0" && "$EDITOR_CMD" == "__" ]]; then
  head_ "dunders"
  if command -v __ >/dev/null 2>&1; then
    say "already installed: $(command -v __)"
  else
    if ! command -v uv >/dev/null 2>&1; then
      say "installing uv…"
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"
    fi
    say "installing dunders…"
    uv tool install --force "dunders[all] @ git+https://github.com/tumikosha/dunders.git"
    command -v __ >/dev/null 2>&1 \
      && say "installed: $(command -v __)" \
      || say "WARNING: __ still not on PATH — add \$HOME/.local/bin to PATH"
  fi
fi

# --- wrapper -----------------------------------------------------------------
head_ "wrapper"
mkdir -p "$TARGET_DIR"
install -m 755 "$SCRIPT_DIR/cc-edit" "$TARGET_DIR/cc-edit"
install -m 755 "$SCRIPT_DIR/cc-session-map" "$TARGET_DIR/cc-session-map"
say "installed cc-edit and cc-session-map into $TARGET_DIR"

# --- hook --------------------------------------------------------------------
head_ "SessionStart hook"
if [[ "$SKIP_HOOK" == "1" ]]; then
  say "skipped — the plugin declares this hook itself"
else
mkdir -p "$HOME/.claude"
[[ -f "$SETTINGS" ]] || printf '{}\n' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-dunders-cc"
HOOK_CMD="$TARGET_DIR/cc-session-map" python3 - "$SETTINGS" <<'PY'
import json, os, sys
p = sys.argv[1]
cmd = os.environ["HOOK_CMD"]
try:
    d = json.load(open(p))
except (json.JSONDecodeError, ValueError):
    print("  settings.json is not valid JSON — leaving it alone", file=sys.stderr)
    raise SystemExit(1)
entries = d.setdefault("hooks", {}).setdefault("SessionStart", [])
# Replace any previous cc-session-map entry rather than stacking duplicates.
entries[:] = [e for e in entries if "cc-session-map" not in json.dumps(e)]
entries.append({"hooks": [{"type": "command", "command": cmd}]})
json.dump(d, open(p, "w"), indent=2)
open(p, "a").write("\n")
PY
say "registered in $SETTINGS (backup: $SETTINGS.bak-dunders-cc)"
fi

# --- shell profile -----------------------------------------------------------
head_ "shell profile"
touch "$PROFILE"
if grep -q "$BEGIN_MARK" "$PROFILE"; then
  cp "$PROFILE" "$PROFILE.bak-dunders-cc"
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    index($0,b) { skip=1 } !skip { print } index($0,e) { skip=0 }
  ' "$PROFILE.bak-dunders-cc" > "$PROFILE"
  say "replaced existing block"
fi
{
  printf '%s\n' "$BEGIN_MARK"
  printf '# ctrl+x ctrl+e opens %s with the session transcript below a sentinel\n' "$EDITOR_CMD"
  printf '# line; cc-edit strips it on exit so only typed text reaches Claude.\n'
  printf 'export EDITOR="%s/cc-edit"\n' "$TARGET_DIR"
  printf 'export CC_REAL_EDITOR="%s"\n' "$EDITOR_CMD"
  printf 'export CC_HISTORY_LINES=200\n'
  printf '%s\n' "$END_MARK"
} >> "$PROFILE"
say "wrote block to $PROFILE (editor: $EDITOR_CMD)"

cat <<EOF

Done.

  Ctrl+G in Claude Code opens $EDITOR_CMD with the transcript;
  Ctrl+G in $EDITOR_CMD saves and exits, sending only what you typed.

Next:
  1. source $PROFILE     (or open a new terminal)
  2. restart claude      — the hook only fires at session start, so sessions
                           already running have no entry in the PID map
  3. press Ctrl+G        — or ctrl+x ctrl+e, which is the same action and
                           survives a custom keybindings.json; type above the
                           marker line, then save and exit

Verify:  tail -20 ~/.claude/cc-edit.log
         a working run logs 'resolved by : session-map/<pid>.json'
EOF
