---
name: setup
description: Installs dunders (the `__` terminal editor) and wires it up as Claude Code's external editor so that ctrl+x ctrl+e opens the current session's transcript for reference while composing a prompt, then strips the transcript on exit so only typed text is sent. Use this skill whenever someone wants to edit Claude Code prompts in a real editor, wants to see or reread conversation history while writing a prompt, complains that ctrl+x ctrl+e opens an empty buffer with no context, asks how to change which editor Claude Code opens, wants to install dunders or the `__` command, or mentions cc-edit, the session transcript, or ~/.claude/projects/*.jsonl — even if they only describe the symptom and never name dunders.
---

# Claude Code external editor with session history

Set up `ctrl+x ctrl+e` in Claude Code so it opens an editor that already
contains the conversation so far. The user writes their prompt at the top; on
exit the history is cut away and only the typed text reaches Claude.

## What the user actually gets

Tell them the key first — it is the same one at both ends, which is the part
people remember:

```
Ctrl+G — the whole loop, one key:
  in Claude Code   Ctrl+G opens your prompt in __, with the session transcript
                   below it
  in the editor    Ctrl+G saves and exits, sending only what you typed
```

`ctrl+x ctrl+e` is the same Claude Code action (`chat:externalEditor`) and is
worth mentioning as the fallback: `Ctrl+G` is only the default binding, so a
user with their own `~/.claude/keybindings.json` may have remapped it. In the
editor `Ctrl+G` is dunders' own Save & Quit — it skips the quit confirmation,
which is exactly why the round trip is one keystroke.

And what the buffer looks like:

```
▌cursor here — write the prompt

════ HISTORY BELOW — everything from this line down is discarded ════

### user
...
### assistant
...
```

## Install

Two paths. Prefer the plugin — it needs no shell at all.

### As a plugin (nothing else to run)

```
/plugin marketplace add tumikosha/dunders
/plugin install dunders
```

Then **start a new session**. Installing does not run the hooks; only a session
start does. On that next start the plugin's `SessionStart` hook runs `cc-wire`,
which copies the wrapper to `~/.claude/dunders-cc/` and points `env.EDITOR` in
`~/.claude/settings.json` at it. The shell profile is never touched.

The restart is for the hook, not for the variable: the `env` block of
settings.json is re-read live, and a variable added to it reaches a session
that started a day earlier. So `~/.claude/dunders-cc/installed.json` is the
thing to check — present means wired, and nothing needs restarting.

Someone who already has the marketplace gets nothing new from `marketplace
add` — it reports success on a clone it leaves at whatever commit it was on.
`/plugin marketplace update dunders` (or a `remove` followed by an `add`) is
what refetches it.

`/plugin uninstall dunders` undoes it: the hook stops running, and the next
`ctrl+x ctrl+e` finds its plugin gone, removes the `settings.json` entry,
deletes `~/.claude/dunders-cc/` and `~/.claude/session-map/`, and opens the file
in a plain editor instead. Cleanup is triggered by that first use, because
Claude Code has no plugin-removal hook — someone who uninstalls and never
presses the key again keeps one directory and one settings line, both inert.

"Its plugin gone" means gone from `~/.claude/plugins/installed_plugins.json`,
not gone from disk: uninstalling leaves the versioned cache directory behind,
marker file and all, so a directory test would read "installed" forever. A
plugin that is merely *disabled* is still installed and does not trigger
removal.

If `ctrl+x ctrl+e` still opens the editor after an uninstall, `EDITOR` is
coming from a shell profile rather than from the plugin — `install.sh` writes
such a block, and a machine that has seen both installs has both. Nothing in
the plugin may edit a user's profile, so the wrapper names the offending file
in `~/.claude/cc-edit.log` on its way out; `bash
skills/setup/scripts/install.sh --uninstall` from a clone removes the block.

`cc-wire` will not take `EDITOR` from anyone: if it is already set to something
that is not ours, the integration stays inert and says so in the log. It still
records the install in `installed.json`, because the wrapper has been copied
into `~/.claude/` either way and has to stay removable.

### From a clone, or to set `$EDITOR` process-wide

Run the bundled installer. It is idempotent — rerunning is how you change the
editor or upgrade, not something to avoid. Use it when `$EDITOR` should point at
the wrapper outside Claude Code too; the plugin path only affects Claude Code.

```bash
bash scripts/install.sh                 # dunders + wrapper
bash scripts/install.sh --editor nvim   # wrapper only, different editor
bash scripts/install.sh --skip-dunders  # dunders already present
bash scripts/install.sh --skip-hook     # installed as a plugin — it owns the hook
bash scripts/install.sh --check         # report state, change nothing
bash scripts/install.sh --uninstall     # remove everything it added
bash scripts/install.sh --purge         # the above plus the log and dunders itself
```

`--uninstall` deliberately leaves dunders installed — people use `__` outside
Claude Code. `--purge` removes it too (from uv and pipx both, since it can be
installed under either or both) and deletes the log. It asks for confirmation
first; `--yes` skips the prompt for unattended runs, and without a terminal it
refuses rather than assuming consent.

It installs `uv` and dunders if `__` is missing, copies `cc-edit` and
`cc-session-map` into `~/.claude/dunders-cc/`, registers a `SessionStart` hook
in `~/.claude/settings.json`, and writes a marked block into the user's shell
profile. Every file it edits gets a `.bak-dunders-cc` backup first.

When this skill arrived through `/plugin install dunders`, the plugin already
declares that hook, so pass `--skip-hook` and leave the user's `settings.json`
alone. Registering it in both places is not fatal — the hook writes the same
record twice — but it leaves an orphaned entry behind when the plugin is
removed.

Afterwards tell the user to do all three of these, because skipping any one of
them makes it look broken:

1. `source` the profile or open a new terminal
2. **restart claude** — the hook fires at session start, so sessions already
   running have no entry in the PID map
3. press `ctrl+x ctrl+e`

## Why it needs a hook at all

This is the part that surprises people, so explain it rather than hand-waving:
Claude Code hands the external editor a stripped environment. `CLAUDE_CODE_SESSION_ID`
is exported to tool-call children but **not** to the editor, so the wrapper
cannot simply read which session it belongs to.

What does reach the editor is `CLAUDE_CODE_MESSAGING_SOCKET`, whose basename is
claude's PID. The `SessionStart` hook records `transcript_path` under that PID,
and the wrapper looks it up. Picking the newest `.jsonl` by modification time
instead does not work — parallel sessions and subagents write to the same
directory, so the freshest file is regularly someone else's.

For the full derivation, the approaches that were tried and rejected, and the
environment observations behind them, read `references/design.md`. Read it when
debugging a broken install or changing how the transcript is located; the
summary above is enough for a plain installation.

## Verifying

Never claim it works without looking at the log — the failure modes are quiet
by design, since a wrapper that printed errors would corrupt the prompt buffer.

```bash
tail -20 ~/.claude/cc-edit.log
```

A healthy run contains `resolved by : session-map/<pid>.json` followed by
`buf after injection` and `stripped ->`.

| Symptom | Meaning |
|---|---|
| No log at all | `$EDITOR` never picked up — profile not sourced, or claude started before it was |
| `transcript : <NOT FOUND>` | Hook has not run; restart claude, check `~/.claude/session-map/` |
| `resolved by : newest-in-…` | Fallback in use, may be the wrong session |
| `!! history render FAILED` | Python error; its stderr follows in the log |
| Marker present, nothing under it | Transcript found but empty — usually a brand-new session |
| `marker absent at exit` | User deleted the marker, so the whole buffer was sent |

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `CC_REAL_EDITOR` | `__` | Which editor to actually run. GUI editors need a wait flag: `code --wait`, `subl -w` |
| `CC_HISTORY_LINES` | `200` | How many recent messages to show |
| `CC_EDIT_DEBUG` | `1` | `0` silences the log |

These live in the managed block of the shell profile. Editing them there and
reopening the shell is enough; no reinstall needed.

## Things worth telling the user

- **`$EDITOR` is global.** `git commit` without `-m` will now open the wrapper
  too. It detects `COMMIT_EDITMSG`, `MERGE_MSG`, `TAG_EDITMSG`,
  `git-rebase-todo`, `*.diff` and `*.patch` and passes them straight through, so
  no transcript ever lands in a commit message. If they want the change confined
  to Claude Code entirely, move the three exports out of the shell profile into
  the `env` block of `~/.claude/settings.json`.
- **The log holds environment dumps.** Secret-shaped variables are redacted,
  because `CLAUDE_CODE_OAUTH_TOKEN` lives in that environment and this log is
  exactly the kind of file people paste into a chat. If a user pastes an
  unredacted log from an older version, tell them to rotate the token.
- **History is per session.** A new session in a new tab shows its own nearly
  empty transcript. That is correct behaviour, not a bug.
- **Editing the history does nothing.** It gets cut away, and the live session's
  context lives in the process's memory anyway — the `.jsonl` is a log, not the
  source of truth.
- **macOS and Linux only.** The wrapper is bash; dunders itself runs on Windows,
  but this integration does not.

## Releasing a change to the plugin

Bump `version` in **both** `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` in the same commit. Claude Code caches an
installed plugin as `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>`
and reuses that directory when the version matches, so shipping new content
under an old version reaches nobody: the user reinstalls, is told the install
succeeded, and keeps running the previous tree. That is exactly how a released
`cc-wire` stayed invisible — the manifest in git declared the hook, the
installed 0.1.0 cache did not contain the script, and `ctrl+x ctrl+e` died with
`ENOENT` on a wrapper nothing had copied into place.

`tests/test_plugin_wiring.py` guards the parts that can be checked from the
repo: the two manifests agree on the version, every declared hook command
exists and is executable, and `cc-wire` is among them.
