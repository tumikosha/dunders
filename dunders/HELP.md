# dunders — quick help

**dunders** is a terminal text editor and Norton Commander / mc-style file
manager in one, built on a Turbo-Vision-inspired windowing layer: two file
panels, multi-window editing with code folding and macros, F3/F4 viewers for
text/markdown/CSV/images/hex/office docs, a virtual filesystem (local, archives,
FTP/SFTP, databases), and an embedded command line with an optional AI assistant.

## Hot keys

### File manager (panel-scoped F-keys)
| Key | Action |
|-----|--------|
| `F1` | Open the menu bar (swapped with F9 vs. the NC default) |
| `F2` | User menu (mc/far-style command menu) |
| `F3` | View file (markdown/CSV/image/hex/office viewers by content) |
| `F4` | Edit file |
| `F5` / `F6` | Copy / Move |
| `F7` / `F8` | Make directory / Delete |
| `F9` | Project View — the 1/4-width file tree (swapped with F1) |
| `Tab` / `Alt+L` / `Alt+R` | Switch / focus left / right panel |
| `Enter` | Open file or enter directory |
| `Alt+.` | Toggle hidden files |
| `Ctrl+B` | Bookmarks (mouse wheel scrolls the list) |
| `Ctrl+D` | Add current location to bookmarks |
| `Alt+H` | Command-line history popup |

### App & windows
| Key | Action |
|-----|--------|
| `F10` | Quit |
| `Esc` | Close window / cancel dialog |
| `Ctrl+P` | Command palette (all available commands) |
| `Ctrl+T` | Cycle theme |
| `Ctrl+U` | Tile windows |
| `Shift+Tab` | Cycle windows |

### Editor
| Key | Action |
|-----|--------|
| `Ctrl+S` | Save |
| `Ctrl+F` | Find |
| `Ctrl+H` | Replace |
| (menu) | Fold / split / macro commands live in the **Editor** menu |

### Database console (`dunders[db]`)
| Key | Action |
|-----|--------|
| `Alt+S` | SQL console (on a db panel) |
| `Ctrl+R` | Run SQL |
| `Alt+H` | SQL history |

## AI features

The optional LLM assistant is configured from the **`_` menu → AI / LLM
settings…** (providers: Anthropic, OpenAI-compatible, Azure, Ollama, groq, etc.;
roles `default/cheap/strong/local/vision`; API keys resolved env-first). No
vendor SDK is required — every provider is reached over stdlib HTTP.

- **Natural-language → command:** type a request in the command line prefixed
  with `#` or `?` (e.g. `# find big log files`) and dunders proposes a shell
  command you can Run / Edit / Cancel.
- **AI command mode:** toggle with `Alt+A` (an `[AI]` hint appears); then plain
  command-line input is treated as natural language.
- **Form editor:** **File → Form editor…** opens a JSON-schema-driven form;
  fields can auto-fill from the clipboard or the editor selection.
- **F12 Agent:** reserved for the interactive agent (work in progress).

See the project README and `docs/` for deeper details.
