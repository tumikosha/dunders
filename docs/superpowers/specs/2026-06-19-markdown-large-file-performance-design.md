# Large Markdown viewer performance — tiered rendering

**Date:** 2026-06-19
**Status:** Approved (design)

## Problem

F3 on a large `.md` freezes the UI for a long time. Measured cause: Textual's
`MarkdownViewer` mounts **one widget per markdown block** (heading, paragraph,
list item, table row), synchronously on the UI thread. Measurements (this repo,
`.venv`, headless `run_test`):

| content | size | Textual render | widgets |
|---|---|---|---|
| prose, 100 paras | 65 KiB | 190 ms | 117 |
| prose, 800 paras | 523 KiB | 1.1 s | 904 |
| list-heavy, 200 sec | 15 KiB | 3.6 s | 3 004 |
| list-heavy, 2000 sec | 153 KiB | **49 s** | 30 004 |

Cost tracks **block/widget count (~1.3 ms/widget)**, not bytes. The `.md`
routing cap is 4 MiB, so the freeze is effectively unbounded.

Two alternatives were measured:

- **Rich markdown in a single `Static`** (`rich.markdown.Markdown`): 153 KiB →
  1.9 s, 616 KiB → 8.1 s, **2 widgets**. ~25× faster, O(1) widgets, but a
  static render — no clickable TOC / link navigation. Cost ~13 ms/KiB.
- **Plain `Static` of raw source**: 1 MiB → 0.9 s, 4 MiB → 4.2 s. Still a
  multi-second freeze for multi-MB.
- **Lazy mmap line-window** (CSV/hex pattern): O(visible) — instant at any size.

A worker thread does **not** help: rendering is CPU-bound Python, the GIL holds
the event loop (measured: 2 UI ticks during a 1.4 s threaded Rich render). So a
"Rendering…" modal cannot animate during a render — freezes must be bounded by
**thresholds**, and huge files must avoid whole-document rendering entirely.

## Goal

Open any `.md` without a long freeze. Keep the current interactive rendered
view (with TOC) for small docs; use a fast static render for block-dense docs;
open multi-MB docs instantly via a lazy line-window, with rendering available on
demand.

## Approach: three tiers by cost model

`MarkdownViewerContent.__init__` becomes cheap: it stat-checks size and computes
a cheap block estimate, then picks a tier. The heavy surface is built on mount,
never in `__init__`. For the huge tier the whole file is **not** read into
memory — it is mmap'd lazily.

Routing for **image-free** documents:

| Tier | Condition | Renderer | Freeze |
|---|---|---|---|
| 1. Interactive | `size ≤ HUGE_CAP` **and** `est_blocks ≤ MAX_BLOCKS` | Textual `MarkdownViewer` + TOC (current behavior) | <0.7 s |
| 2. Rich | `size ≤ HUGE_CAP`, `est_blocks > MAX_BLOCKS` | `rich.markdown.Markdown` in one `Static` | ~1–2 s |
| 3. Huge | `size > HUGE_CAP` | Lazy line-window over the raw source (mmap) | instant |

**Thresholds** (module constants, tunable):

- `HUGE_CAP = 128 * 1024` (128 KiB). Above this, tier 3.
- `MAX_BLOCKS = 600`. At ~1.3 ms/widget this bounds tier 1 to <~0.8 s.
- `RICH_RENDER_HARD_CAP = 1024 * 1024` (1 MiB). Opt-in render limit in tier 3.

**Asymmetry rationale:** mis-routing a file to a faster tier costs only the TOC;
mis-routing to a slower tier costs a freeze. So the estimate is allowed to
over-count (bias toward the faster tier) and thresholds stay conservative.

## Components

### `estimate_blocks(source: str) -> int` (pure)

Cheap proxy for the widget count Textual would create, without parsing. Counts
block-level lines: ATX headings (`#`), list items (`-`/`*`/`+`/`N.`), table
rows (contain `|`), blockquotes (`>`), fenced-code fences, and paragraph starts
(a non-blank line preceded by a blank line or start-of-file). One pass over
`splitlines()`; no markdown parse. Lives in `markdown_viewer.py`, unit-tested in
isolation. It may over-count (acceptable per the asymmetry above).

For the huge tier the full text is not in memory, so the estimate is **not**
computed there — size alone routes to tier 3.

### Shared lazy line source — `dunders/fm/line_source.py` (new)

Extract `_LineSource` / `_TextSource` / `_MmapSource` (and the prefix-index
constant) verbatim from `csv_viewer.py` into a shared module, renamed without
the leading underscore (`LineSource`, `TextSource`, `MmapSource`) since they now
have two consumers. `csv_viewer.py` imports them back (its existing private
aliases can re-point to the shared names to minimise churn). No behavior change;
the CSV tests continue to cover the mmap/index logic.

### Tier 3 surface — lazy raw text view

A small `ScrollView` (`_LazyTextView`, in `markdown_viewer.py`) backed by a
`LineSource`: virtual size = `line_count`, `render_line(y)` decodes and returns
only the visible line. Mirrors `CsvViewerWidget`'s lazy rendering, minus column
parsing. Background index growth uses the source's existing `index_batch` so the
scrollbar settles without blocking. `can_focus = True` so keys/wheel scroll on
mount (consistent with the existing focus fix).

The huge tier opens here by default (instant). The toolbar shows an opt-in
**[ Render ]** button **only when `size ≤ RICH_RENDER_HARD_CAP`**: pressing it
builds the Rich-in-`Static` surface (accepting a bounded, user-initiated
freeze). Above the hard cap the button is omitted (no whole-file render path).

### Tier 2 surface — Rich in a `Static`

`VerticalScroll(Static(rich.markdown.Markdown(source)))`, `can_focus = True`. No
TOC button (Rich has no aggregated outline — same omission already made for the
composed image renderer). Raw toggle still works (the existing `_raw_view`).

### Tier 1 surface — unchanged

Current Textual `MarkdownViewer` path, including the `can_focus = True` fix and
the TOC button.

## Lazy construction

`__init__` must not do heavy work. It stat-checks the size; for non-huge it
reads the text and computes `estimate_blocks`; it records the chosen tier and
the cheap inputs only. `compose`/`on_mount` builds the chosen surface. This
keeps window open latency low and the freeze confined to the (bounded) render.

## Images

Documents containing inline images keep the current composed renderer
(`_build_document`) regardless of size — Rich cannot draw inline ASCII art and
the lazy raw view shows source, not pictures. This is a known limitation; the
new tiering applies to image-free documents only. (A very large image doc is
rare; if it becomes a problem, falling such docs back to tier 3 lazy-raw is a
follow-up, not part of this work.)

## Error handling

- Unreadable file → the existing `# Could not read file` placeholder (tier 1
  path), unchanged.
- mmap failure in tier 3 → fall back to reading the text and using tier 2 (Rich)
  if `size ≤ RICH_RENDER_HARD_CAP`, else a plain `Static` of the source.

## Testing

- **Unit (`estimate_blocks`):** prose, list-heavy, tables, code fences,
  headings, empty — assert it tracks block count and over-counts rather than
  under-counts on list/table-heavy input.
- **Routing:** construct `MarkdownViewerContent` with sources/sizes straddling
  each threshold; assert the chosen tier (interactive / rich / lazy) without
  mounting 30k widgets. Inject size for the huge case via a temp file.
- **Lazy view:** a multi-MB temp `.md` opens with a lazy view and without
  reading the whole file into memory (assert the surface is `_LazyTextView` and
  scrolling renders correct lines); the opt-in **[ Render ]** appears only below
  `RICH_RENDER_HARD_CAP`.
- **Shared line source:** existing CSV tests cover the mmap/index logic after
  the move; add a direct import test from `line_source`.
- **Regression:** the existing markdown-viewer suite (focus-on-mount, raw/TOC
  toggles, inline images, `from_bytes`) stays green.

## Files touched

- `dunders/fm/line_source.py` — new shared `LineSource`/`TextSource`/`MmapSource`.
- `dunders/fm/csv_viewer.py` — import the shared sources (drop the local copies).
- `dunders/fm/markdown_viewer.py` — `estimate_blocks`, tier routing, lazy
  construction, `_LazyTextView`, Rich tier, opt-in render.
- `dunders/app.py` — no routing change expected (the 4 MiB `.md` cap stays; the
  viewer now handles large files itself). Verify member path still uses
  `from_bytes`/`from_text`.
- `tests/fm/test_markdown_viewer.py`, `tests/fm/test_line_source.py` (new) —
  tests above.
- `CLAUDE.md` — document the tiered renderer and the shared line source.
