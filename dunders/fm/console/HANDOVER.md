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

## Possible future fixes (not done)

1. Implement mc-style tty handover for the persistent subshell so `relay` can
   host kitty-protocol TUIs (deep, uncertain).
2. Auto-detect interactive/full-screen programs and route only those through
   `suspend`, keeping `relay` for plain shell commands.
3. Make `suspend` the default and treat `relay` as opt-in.
