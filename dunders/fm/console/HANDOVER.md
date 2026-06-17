# Terminal handover: `relay` vs `suspend` (and why full-screen TUIs differ)

> **UPDATE (2026-06-07):** `relay` now hosts full-screen / kitty-protocol TUIs
> correctly (claude `Shift+Enter` works) while keeping its persistent subshell.
> The fix moved command-completion detection off the program's stdout stream
> onto a dedicated **FIFO side-channel** (mc-style), so the byte bridge forwards
> **verbatim** — no scanning, no 64-byte holdback. The real root cause was the
> holdback in `scan_sentinel`, not the nested PTY itself (see "Root cause"
> below, now superseded). "Possible future fix #1" is **DONE**. See
> `docs/superpowers/specs/2026-06-07-relay-kitty-transparent-handover-design.md`
> and the matching plan. The sections below are kept for historical context.

## Symptom

Running a full-screen TUI program from dunders's command line — most notably
`claude` — works in some configurations and misbehaves in others:

- **Real `mc`** (Midnight Commander): launch `claude`, everything works,
  including `Shift+Enter` (newline in a multi-line prompt).
- **dunders `wew`** (suspend handover): works the same — `Shift+Enter` is fine.
- **dunders `we`** (relay handover): typing works, but `Shift+Enter` produces
  **garbage characters in claude's output area**.

The artifacts appear in *claude's* rendering, not in dunders's command-line input
field. dunders's own key decoding is fine: the Key Probe (`Help → Key Probe`)
shows `Shift+Enter` arriving as `key=alt+enter` (PyCharm's terminal sends
`ESC`+`CR`), which the command line already maps to "insert newline".

## Root cause

claude is a full-screen TUI. It needs a **real terminal**: cursor
addressing, scroll regions, and — critically here — the **kitty keyboard
protocol**, which is what lets it distinguish `Shift+Enter` from `Enter`.

dunders has two ways to give a program a terminal (`dunders/fm/console/handover.py`):

| Mode | Class | How the program gets its terminal |
| --- | --- | --- |
| `suspend` (`wew`) | `SubprocessHandover` | `subprocess.run(cmd, shell=True)` inside `app.suspend()`. The child **inherits the real tty directly.** |
| `relay` (`we`) | `RelayHandover` | One persistent `$SHELL -i` lives in a **nested PTY**; the real terminal is put in raw mode and bytes are bridged to/from that PTY. |

In `suspend` mode claude talks straight to the real terminal, so it negotiates
the kitty keyboard protocol with the host terminal (PyCharm) and gets a
distinct `Shift+Enter`. In `relay` mode claude runs one PTY layer removed: the
kitty-protocol negotiation has to survive the nested-PTY byte bridge, and in
practice it does not pass through cleanly — claude falls back to legacy key
encoding where `Shift+Enter` collapses to an ambiguous `ESC`+`CR`, and its
redraw of the multi-line input renders as garbage.

## Why we can't trivially "just fix relay"

The relay design exists on purpose: a **persistent subshell** keeps session
state (`cd`, `export`, shell history) alive *between* commands, even though
Textual owns the real terminal in the meantime. Keeping a shell alive while
Textual holds the terminal requires parking that shell on a separate PTY — and
that nested PTY is exactly what breaks transparent passthrough of the kitty
protocol.

So there is an inherent tension in the current architecture:

- **Persistent session** ⇒ nested PTY ⇒ breaks kitty-protocol TUIs.
- **Working full-screen TUIs** ⇒ direct tty ⇒ no persistent env between commands.

Real `mc` gets both because it hands the *real* terminal to the foreground
child via a more elaborate tty-passing mechanism (its `cons.saver` machinery)
that dunders does not currently implement.

## Current behaviour (as of this note)

- Typed commands and "run executable" (Enter/double-click on an executable in a
  panel) both go through the **handover** layer in every launch mode
  (`fm`/`we`/`we-mc`/…), not the thin embedded relay console — see
  `DundersApp._run_in_console`, `_ensure_handover`, `_run_handover_command`.
- Which handover is used is `DundersApp.terminal_mode` (`relay` or `suspend`).
- A **run-mode switch sits next to the command line** (`CommandLine`'s mode
  chip). Click it to flip `relay ⇄ suspend` at runtime; the next command runs
  in the new mode. Use `suspend` for full-screen TUIs like claude.

**Recommendation for now:** to run claude (or any full-screen TUI), switch the
run-mode chip to `suspend` (equivalent to launching with `wew`).

## Ctrl+O detach / reattach (mc-style background of a running command)

In a panel mode (`fm`/`we-mc`) `relay`, Ctrl+O while a foreground command is
running **detaches** from it (back to the panels) and a later Ctrl+O
**reattaches** — Midnight-Commander parity. Implementation lives in
`RelayHandover`:

- `_pump` intercepts the Ctrl+O byte (`_TOGGLE = 0x0F`) and returns `None`
  (detached, command still alive) instead of an exit code. `run_foreground`
  then sets `_suspended_cmd` and returns to the panels without capturing cwd.
- `command_screen` sees `_suspended_cmd` and **reattaches** by pumping the same
  persistent subshell again (it must NOT `cd`/`_sync_cwd` — those bytes would
  land *inside* the running child as keystrokes).
- Ctrl+O is **reserved** for the toggle, so a child that uses Ctrl+O itself
  (e.g. nano = save) won't receive it — exactly as under `mc`.

### Gotcha: reattaching to a full-screen TUI needs a *real* resize

> **Why the obvious fixes fail.** Textual's `App.suspend()` emits the
> alt-screen-exit `\x1b[?1049l` on entry, so inside the suspend block we are on
> the **normal** screen — but the child entered the **alternate** screen at
> startup and never left it. On reattach you must therefore re-enter the alt
> screen (`\x1b[?1049h`) so the child's cursor-addressed repaint lands on the
> right buffer (`_prime_reattach`), and `\x1b[?1049l` on the way out
> (`_end_reattach`) so the suspend block ends symmetric with a normal run.
>
> **The key insight:** a diff-rendering TUI (Ink — what `claude` uses)
> re-renders **only on a genuine terminal-size change**. A same-size `SIGWINCH`
> is ignored, so just signalling the child leaves the screen blank (we cleared
> the alt buffer but the child never repainted). The fix is to *actually* change
> the pty size and restore it — `setwinsize(rows-1, cols)`, a short
> `time.sleep` so the two `SIGWINCH`s aren't coalesced into one no-op, then
> `setwinsize(rows, cols)`. Each real resize makes Ink rewrite its whole frame
> (Ink always writes the full frame, never a delta), so the child reappears.
> Signalling via `tcgetpgrp`/`killpg` is NOT reliable (wrong group on macOS, and
> still a no-op when the size is unchanged).

A robust, app-independent reattach (tmux-style) would require dunders to run its
own terminal emulator over the child's PTY continuously while detached and
repaint from that saved buffer — a much larger feature, not done.

## Possible future fixes (not done)

1. Implement mc-style tty handover for the persistent subshell so `relay` can
   host kitty-protocol TUIs (deep, uncertain).
2. Auto-detect interactive/full-screen programs and route only those through
   `suspend`, keeping `relay` for plain shell commands.
3. Make `suspend` the default and treat `relay` as opt-in.
