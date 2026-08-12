# dunders

> **We ship the underscores. You write what goes between them.**

An open-source **terminal platform** built on
[Textual](https://textual.textualize.io/), where _everything is a panel_. At its
core it's a Norton Commander–style dual-pane manager with an embedded text
editor and an LLM-agent CLI — but the bigger idea is the blank between the
underscores: the core ships `__`, and you fill in the rest.

Two panels aren't really about files. They're about any two sets of objects you
can copy between — folders, archives, remote/cloud filesystems, containers,
databases, API responses. `dunders` brings back the dual-pane workflow of `mc` /
Far Manager — with a real windowing layer (Turbo Vision–inspired), code folding,
recordable macros, a command palette, and an embedded LLM/agent CLI mode — and
makes that surface extensible.

The CLI command is **`__`** or **`dunders`** ; the `__` is the platform's blank,
waiting to be filled.

> Status: **alpha**. Core file-manager and editor are usable; agent/CLI
> mode is a stub.

## Quick install (any OS — one line)

Installs [`uv`](https://docs.astral.sh/uv/) if you don't have it, then installs
`dunders` (plus the `__` / `__w` launchers and every opt-in dunder — `sftp:`,
the `db:` database dunder, image and office/PDF viewers — via the `[all]` extra)
into an isolated environment — no system Python needed.

**Linux / macOS / WSL** (bash/zsh):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH" && uv tool install --force "dunders[all] @ git+https://github.com/tumikosha/dunders.git"
```

**Windows** (PowerShell):

```powershell
irm https://astral.sh/uv/install.ps1 | iex; $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"; uv tool install --force "dunders[all] @ git+https://github.com/tumikosha/dunders.git"
```

Then run `dunders` or just "__". (Already have `uv`? Just `uv tool install "dunders[all] @ git+https://github.com/tumikosha/dunders.git"`.)

Want a leaner install? Pick only the extras you need instead of `[all]` —
e.g. `dunders[db]` (database only), `dunders[sftp,db]`, or plain `dunders` for
the base file manager + editor. Available extras: `sftp`, `db`, `image`,
`office`, `all`.

## Features

- **Dual-pane file manager** powered by AI with sort, multi-select, quick-search, and
  the classic NC F-key bar (F3 view, F4 edit, F5 copy, F6 move, F7 mkdir,
  F8 delete, F9 menu, F10 quit).
- **Embedded text editor** with split view, search & replace, fold-by-indent,
  and bracket/region folding rules.
- **Recordable macros** with persistent storage.
- **Hex viewer** for binary or large files (mmap-backed, switches in
  automatically above 4 MiB).
- **Turbo Vision–style windowing layer** (`dunders.windowing`) — reusable in
  other Textual apps. Tile, cascade, maximize, modal dialogs, command
  palette, themable via YAML.
- **Mouse support** everywhere, including the menu bar and status bar.
- **LLM agent / CLI mode** (in progress) — bring your own model.

## Install

### Zero-Python install via [uv](https://docs.astral.sh/uv/) (recommended)

`uv` is a single static binary. It installs Python for you, then installs
`dunders` into an isolated environment and puts the `dunders` command on your
`PATH`. No system Python required.

```bash
# 1. Install uv (one-liner, no Python needed)
curl -LsSf https://astral.sh/uv/install.sh | sh        # macOS / Linux
# Windows PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Or via package manager: brew install uv  /  pipx install uv  /  scoop install uv

# 2. Install dunders (uv fetches Python 3.12+ automatically if missing)
uv tool install dunders

# 3. Run
__ 
dunders
```

Try it once without installing:

```bash
uvx --from dunders dunders          # downloads, runs in a temp env, then forgets
```

Upgrade / uninstall:

```bash
uv tool upgrade dunders
uv tool uninstall dunders
```

### If you already have Python 3.12+

```bash
pipx install dunders            # preferred — isolated, on $PATH
# or, inside an active venv:
pip install dunders
```

Requires Python 3.12+ in any path above.

## Usage

```bash
__
dunders                  # two-panel file manager (default)
dunders path/to/dir      # file manager seeded at a directory
dunders path/to/file     # open a file in the editor
dunders --cli            # agent / CLI mode (stub)
```

### Short launchers

Two extra console scripts are installed alongside `dunders`, differing only in
how embedded shell commands hand off the terminal:

| Command | Terminal mode | Platform |
| ------- | ------------- | -------- |
| `__`    | **relay** — persistent relay subshell | Linux / macOS |
| `__w`   | **suspend** — suspend + subprocess, no persistent session | cross-platform (use this on Windows) |

Both take the same arguments (files open in cascaded editor windows; a lone
directory or no args open the mc-style file manager) and accept `--suspend`
explicitly; `__w` is just `__ --suspend`.

Inside the app:

| Key             | Action                              |
| --------------- | ----------------------------------- |
| `F3`            | View file (hex if binary/large)     |
| `F4`            | Edit file                           |
| `F5` / `F6`     | Copy / Move selected items          |
| `F7` / `F8`     | Mkdir / Delete                      |
| `F9` / `F10`    | Menu / Quit                         |
| `Tab`           | Switch panel                        |
| `Shift+Tab`     | Cycle desktop windows               |
| `Alt+L / Alt+R` | Focus left / right panel            |
| `Ctrl+K`        | Command palette                     |

Editor-scoped keys (Save, Find/Replace, Split, Fold, Record macro) appear in
the status bar when an editor window has focus.

## Claude Code integration

**`Ctrl+G` — the whole loop, one key.** In Claude Code it opens your prompt in
`__` with the session transcript below it; in `__` it saves and exits, sending
only what you typed.

Use `__` as [Claude Code](https://claude.com/claude-code)'s external editor, with
the current session's transcript loaded alongside your prompt. `Ctrl+X Ctrl+E`
is the same Claude Code action (`chat:externalEditor`) if you have remapped
`Ctrl+G` in your own `keybindings.json`. The editor opens like this:

```
▌cursor here — write your prompt

════ HISTORY BELOW — everything from this line down is discarded ════

### user
...
### assistant
...
```

Write above the marker, save, exit. The history is cut away before Claude reads
the file back, so only your text is sent — the transcript is there to read, not
to resend.

### Install — as a Claude Code plugin

Inside Claude Code:

```
/plugin marketplace add tumikosha/dunders
/plugin install dunders
```

Then restart claude. That is the whole installation — no script to run, no shell
profile to edit. The plugin's `SessionStart` hook copies the wrapper to
`~/.claude/dunders-cc/` and points `env.EDITOR` in `~/.claude/settings.json` at
it, which is the one thing a plugin manifest cannot declare on its own.

```
/plugin uninstall dunders
```

undoes it. The hook stops running; the next `ctrl+x ctrl+e` notices the plugin
is gone, removes the `settings.json` entry, deletes the directories it created,
and opens your file in a plain editor. Cleanup happens on that first use rather
than at uninstall, because Claude Code has no plugin-removal hook — uninstall
and never press the key again and one inert directory and one settings line stay
behind.

An `EDITOR` you set yourself is never overwritten; the integration stays inert
and logs why.

### Install — from a clone

```bash
git clone https://github.com/tumikosha/dunders dunders
bash dunders/skills/setup/scripts/install.sh
```

To make the skill available to Claude without the plugin system, symlink it —
edits in the clone then take effect without reinstalling:

```bash
mkdir -p ~/.claude/skills
ln -s "$PWD/dunders/skills/setup" ~/.claude/skills/
```

The installer fetches `uv` and dunders if `__` is missing, drops two scripts into
`~/.claude/dunders-cc/`, registers a `SessionStart` hook in
`~/.claude/settings.json`, and adds a marked block to your shell profile. It
backs up every file it touches (`*.bak-dunders-cc`) and is safe to rerun.

```bash
install.sh --editor nvim     # wire up a different editor instead of __
install.sh --skip-dunders    # wrapper only
install.sh --check           # report current state, change nothing
install.sh --uninstall       # remove everything it added
install.sh --purge           # the above plus the log and dunders itself
```

`--uninstall` leaves dunders in place — `__` is useful outside Claude Code.
`--purge` also removes it (from both uv and pipx) and deletes
`~/.claude/cc-edit.log`; it confirms first, or takes `--yes` for scripts.

Then: open a new terminal, **restart `claude`** (the hook fires at session start,
so already-running sessions have no entry in the PID map), and press
`Ctrl+X Ctrl+E`.

### Configuration

Set in the managed block of your shell profile:

| Variable | Default | Effect |
| -------- | ------- | ------ |
| `CC_REAL_EDITOR` | `__` | Editor to run. GUI editors need a wait flag: `code --wait`, `subl -w` |
| `CC_HISTORY_LINES` | `200` | How many recent messages to show |
| `CC_EDIT_DEBUG` | `1` | `0` silences `~/.claude/cc-edit.log` |

### Notes

- `$EDITOR` is global, so `git commit` without `-m` opens the wrapper too. It
  detects commit, merge, tag, rebase and diff buffers and passes them through
  untouched — no transcript ever lands in a commit message. To confine the
  change to Claude Code, move the exports into the `env` block of
  `~/.claude/settings.json` instead.
- History is per session; a fresh session shows its own, nearly empty transcript.
- If something looks wrong, `tail -20 ~/.claude/cc-edit.log` — a healthy run logs
  `resolved by : session-map/<pid>.json`.
- macOS and Linux. The wrapper is bash; `__w` still covers Windows for normal
  editing, but this integration does not.

The directory also ships as a Claude Code skill — copy or symlink
`skills/setup` into `~/.claude/skills/` and Claude can perform the
setup and troubleshooting itself. `skills/setup/references/design.md`
documents how the transcript is located and why the obvious approaches fail.

## Development

```bash
git clone https://github.com/tumikosha/dunders dunders
cd dunders
uv sync --extra dev          # or: pip install -e '.[dev]'

pytest                       # full suite
pytest -k fold_engine        # by keyword
ruff check
```

The repository ships a standalone windowing demo to exercise the framework
without the file-manager layer:

```bash
python -m dunders.windowing.demo
```

## Project layout

The PyPI distribution is named **`dunders`**; the importable Python package and
the CLI command are both **`dunders`**.

```
dunders/
├── app.py            # DundersApp shell — wires menus, panels, dispatcher
├── main.py           # entry point (argparse)
├── fm/               # file-manager domain (panels, dialogs, file ops)
├── windowing/        # Turbo Vision–style framework on Textual
│   ├── core/         # buffer, fold engine, macros, search
│   ├── editor/       # embeddable editor widget + content
│   ├── themes/       # palette loader + modern_dark default
│   └── demo/         # standalone framework demo
├── themes/           # dark.yaml / light.yaml palettes
└── config/defaults.py
```

Alongside the package, the repository root carries `skills/` — Claude Code skills
shipped with the project, currently `setup` (see
[Claude Code integration](#claude-code-integration)).

See [`CLAUDE.md`](./CLAUDE.md) for an architecture deep-dive aimed at
contributors and AI coding assistants.

## Terminal limitations on macOS

macOS **Terminal.app** does not report several modifier+key combinations to
the application, so some editor shortcuts can't reach `dunders` there:

- `Shift+↑` / `Shift+↓` / `Shift+Home` / `Shift+End` — selection by line / to
  start/end of line. Terminal.app sends the same sequence as the unmodified
  key, so the selection variant never arrives.
- `Cmd+C`, `Cmd+↑` / `Cmd+↓` — the terminal intercepts `Cmd` shortcuts itself
  and never forwards them.

You can confirm what your terminal sends with `cat -v` (press the combo, then
`Ctrl+C` to quit): if `Shift+↑` prints `^[[A` (same as plain `↑`) the modifier
is being dropped.

**Two fixes:**

1. **Use a terminal that supports the kitty keyboard protocol** — iTerm2,
   Ghostty, Kitty, WezTerm. These deliver `Shift+arrows` and `Cmd+arrows`/`Cmd+C`
   out of the box, no configuration needed. (Recommended.)

2. **Remap the keys in Terminal.app** — Settings → Profiles → *your profile* →
   **Keyboard** → **+**, with Action *Send Text* (`\033` is the Esc character):

| Key    | Modifier | Send Text   |
|--------|----------|-------------|
| `↑`    | Shift    | `\033[1;2A` |
| `↓`    | Shift    | `\033[1;2B` |
| `Home` | Shift    | `\033[1;2H` |
| `End`  | Shift    | `\033[1;2F` |

`Cmd+C` can't be remapped this way (Terminal.app keeps it for its own Copy). In
the editor and command line use `Ctrl+C` to copy instead — in the command line
`Ctrl+C` copies the current selection and otherwise cancels/clears, like a
shell.

## License

MIT — see [LICENSE](./LICENSE).
