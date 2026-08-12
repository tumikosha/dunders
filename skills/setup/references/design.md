# Design notes: cc-edit

Why the integration is shaped the way it is, and what was ruled out along the
way. Read this when debugging a broken install, porting the idea, or changing
how the transcript gets located.

## The problem

Claude Code has two things that look like they should do this already, and
neither does:

- **`ctrl+o` transcript view.** Pressing `v` inside it dumps the conversation to
  a temp file and opens an editor, but nothing reads that file back afterwards.
  It is a viewer; edits die with the temp file.
- **`ctrl+x ctrl+e`** (action `chat:externalEditor`, also bound to `ctrl+g`).
  This opens an editor on the **chat input buffer**, and whatever remains in the
  file at exit is sent to Claude. History is never put there.

The goal is to combine them: see the conversation while composing, without
sending it back.

## Why not keybindings

Claude Code's action list contains nothing like "open the transcript in an
editor", and keybindings can only remap existing actions — they cannot run an
arbitrary command. The relevant actions are:

| Action | Default keys |
|---|---|
| `chat:externalEditor` | `ctrl+x ctrl+e`, `ctrl+g` |
| `app:toggleTranscript` | `ctrl+o` |
| `transcript:toggleShowAll` | `ctrl+e` (inside the transcript) |

So the logic has to live in the editor rather than in the binding. That is what
makes an `$EDITOR` wrapper the right shape: it operates on the very temp file
that `chat:externalEditor` round-trips.

## Flow

```
ctrl+x ctrl+e
   │
   ├─ Claude Code writes the input buffer to
   │  /tmp/claude-<uid>/claude-prompt-<uuid>.md and runs $EDITOR on it
   │
   ├─ cc-edit:
   │     1. guard — git buffer? hand straight to the real editor
   │     2. locate this session's transcript
   │     3. append sentinel + rendered history to the buffer
   │     4. run $CC_REAL_EDITOR
   │     5. on exit, awk cuts everything from the sentinel down
   │
   └─ Claude Code reads the file → receives only the user's text
```

## Locating the transcript

Transcripts live at `~/.claude/projects/<slug>/<session-id>.jsonl`, where `slug`
is the project path with `/`, `_` and `.` all replaced by `-`. So
`/Users/tumi/prj_python/dunders` becomes `-Users-tumi-prj-python-dunders`.

### Approaches that do not work

- **Newest file by mtime.** Parallel sessions and subagents write into the same
  directory. Observed directly during development: the most recently modified
  transcript belonged to a different session than the one asking.
- **`CLAUDE_CODE_SESSION_ID` from the environment.** It is exported only to
  tool-call children, which are additionally marked with
  `CLAUDE_CODE_CHILD_SESSION=1`. The external editor gets a stripped
  environment — five `CLAUDE_*` variables instead of ten, with no session id.
  This one is a trap: probing the environment from a shell tool call shows the
  variable present, which does not generalise to the editor.
- **The UUID in the temp file name.** `claude-prompt-<uuid>.md` identifies a
  single editor invocation, not the session.
- **`lsof` on the claude process.** The transcript is not held open; writes are
  short appends that close the handle.

### What works

`CLAUDE_CODE_MESSAGING_SOCKET=/tmp/cc-socks/<pid>.sock` does reach the editor,
and that `<pid>` is the claude process — the same number as the editor's own
`PPID`. That gives a stable key, and a `SessionStart` hook supplies the value:
its stdin payload contains `transcript_path` directly.

```
socket → PID → ~/.claude/session-map/<pid>.json → transcript_path
```

`cc-session-map` writes the record under every PID it can derive (socket
basename, `CLAUDE_PID`, `getppid()`) so the lookup succeeds regardless of how
the hook itself was spawned. It also prunes entries whose process is gone, so
the directory cannot grow without bound.

### Resolution order in cc-edit

| Order | Method | When it applies |
|---|---|---|
| 1 | `session-map/<pid>.json` | normal operation |
| 2 | glob by `CLAUDE_CODE_SESSION_ID` | if that variable is ever exported here |
| 3 | newest `.jsonl` in the cwd's project dir | hook did not run; ambiguous with several sessions per directory |

Whichever fired is recorded in the log as `resolved by : …`.

## Rendering

From the `.jsonl`, records with `type` of `user` or `assistant` are kept,
`isMeta` and `isSidechain` are skipped, and only **text blocks** are read out of
the content. Dropping `tool_use` and `tool_result` is not cosmetic: a real
transcript weighs megabytes with them and kilobytes without, and neither is
useful when the point is to reread the conversation. Bodies starting with `<`
are dropped too — those are wrappers like `<system-reminder>`.

## Guards

### git

`$EDITOR` is inherited by every child process Claude spawns, including
`git commit` without `-m`. A transcript appended to a commit message would be a
serious mess, so `COMMIT_EDITMSG`, `MERGE_MSG`, `TAG_EDITMSG`, `git-rebase-todo`,
`*.diff`, `*.patch` and any path containing `/.git/` are handed to the real
editor untouched.

Confining the change to Claude Code entirely is possible instead: move the
exports from the shell profile into the `env` block of `~/.claude/settings.json`.

### Secrets in the log

The diagnostic log dumps the `CLAUDE_*` environment, which contains
`CLAUDE_CODE_OAUTH_TOKEN` — a live credential for the user's Anthropic account.
Values of variables whose names contain `TOKEN`, `SECRET`, `KEY`, `PASSWORD` or
`AUTH` are replaced with `<redacted>`.

This was learned the hard way: the log's whole purpose is to be pasted into a
chat when something breaks, so redaction has to exist before the first support
request, not after.

## Failure modes

Errors cannot be printed to stdout or stderr in the normal path — the wrapper
runs attached to the terminal Claude Code is drawing into, and stray output
corrupts the buffer. Hence the log file, on by default.

| Log line | Meaning |
|---|---|
| (no log) | `$EDITOR` never picked up |
| `transcript : <NOT FOUND>` | hook has not run for this session |
| `resolved by : newest-in-…` | fallback 3, possibly the wrong session |
| `!! history render FAILED` | python error; stderr follows |
| `marker absent at exit` | sentinel deleted by hand, whole buffer sent |

## Limitations

- Current session only. A new session shows its own, nearly empty transcript.
- The most recent messages land in the `.jsonl` after a flush delay, so the very
  last turn may be missing.
- Fallback 3 is ambiguous when several sessions share a working directory.
- Editing the history is pointless: it is cut away, and the live context lives in
  the process's memory. The `.jsonl` is a log, not the source of truth.
- bash-only, so macOS and Linux. dunders itself runs on Windows; this wrapper
  does not.
