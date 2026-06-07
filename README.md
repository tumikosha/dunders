# tyui

> **T**erminal · **Y**our · **U**niversal · **I**ntelligence

A Norton Commander–style terminal file manager, text editor, and LLM-agent
CLI for the modern shell — built on
[Textual](https://textual.textualize.io/).

The name `tyui` is the four QWERTY keys right after `qwer`y — picked in the
same spirit as vim's `hjkl`. The CLI command is **`tyui`**.

`tyui` brings back the dual-pane workflow of `mc` / Far Manager — but with a
real windowing layer (Turbo Vision–inspired), code folding, recordable
macros, a command palette, and an embedded LLM/agent CLI mode.

> Status: **alpha**. Core file-manager and editor are usable; agent/CLI
> mode is a stub.

## Quick install (any OS — one line)

Installs [`uv`](https://docs.astral.sh/uv/) if you don't have it, then installs
`tyui` (plus the `we` / `wew` launchers) into an isolated environment — no
system Python needed.

**Linux / macOS / WSL** (bash/zsh):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH" && uv tool install --force git+https://github.com/tumikosha/tyui.git
```

**Windows** (PowerShell):

```powershell
irm https://astral.sh/uv/install.ps1 | iex; $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"; uv tool install --force git+https://github.com/tumikosha/tyui.git
```

Then run `tyui`. (Already have `uv`? Just `uv tool install git+https://github.com/tumikosha/tyui.git`.)

## Features

- **Dual-pane file manager** with sort, multi-select, quick-search, and
  the classic NC F-key bar (F3 view, F4 edit, F5 copy, F6 move, F7 mkdir,
  F8 delete, F9 menu, F10 quit).
- **Embedded text editor** with split view, search & replace, fold-by-indent,
  and bracket/region folding rules.
- **Recordable macros** with persistent storage.
- **Hex viewer** for binary or large files (mmap-backed, switches in
  automatically above 4 MiB).
- **Turbo Vision–style windowing layer** (`tyui.windowing`) — reusable in
  other Textual apps. Tile, cascade, maximize, modal dialogs, command
  palette, themable via YAML.
- **Mouse support** everywhere, including the menu bar and status bar.
- **LLM agent / CLI mode** (in progress) — bring your own model.

## Install

### Zero-Python install via [uv](https://docs.astral.sh/uv/) (recommended)

`uv` is a single static binary. It installs Python for you, then installs
`tyui` into an isolated environment and puts the `tyui` command on your
`PATH`. No system Python required.

```bash
# 1. Install uv (one-liner, no Python needed)
curl -LsSf https://astral.sh/uv/install.sh | sh        # macOS / Linux
# Windows PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Or via package manager: brew install uv  /  pipx install uv  /  scoop install uv

# 2. Install tyui (uv fetches Python 3.12+ automatically if missing)
uv tool install tyui

# 3. Run
tyui
```

Try it once without installing:

```bash
uvx --from tyui tyui          # downloads, runs in a temp env, then forgets
```

Upgrade / uninstall:

```bash
uv tool upgrade tyui
uv tool uninstall tyui
```

### If you already have Python 3.12+

```bash
pipx install tyui            # preferred — isolated, on $PATH
# or, inside an active venv:
pip install tyui
```

Requires Python 3.12+ in any path above.

## Usage

```bash
tyui                  # two-panel file manager (default)
tyui path/to/dir      # file manager seeded at a directory
tyui path/to/file     # open a file in the editor
tyui --cli            # agent / CLI mode (stub)
```

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

## Development

```bash
git clone https://github.com/tumikosha/qwe tyui
cd tyui
uv sync --extra dev          # or: pip install -e '.[dev]'

pytest                       # full suite
pytest -k fold_engine        # by keyword
ruff check
```

The repository ships a standalone windowing demo to exercise the framework
without the file-manager layer:

```bash
python -m tyui.windowing.demo
```

## Project layout

The PyPI distribution is named **`tyui`**; the importable Python package and
the CLI command are both **`tyui`**.

```
tyui/
├── app.py            # TyuiApp shell — wires menus, panels, dispatcher
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

See [`CLAUDE.md`](./CLAUDE.md) for an architecture deep-dive aimed at
contributors and AI coding assistants.

## License

MIT — see [LICENSE](./LICENSE).
