# Textual UI cookbook — dunders dialogs

Recurring fixes for theming and layout of Textual widgets inside the project's
Turbo-Vision-style dialogs (`fm/*_dialog.py`, built on `windowing`). These are
the "доделки UI" that keep coming back — check here **before** hand-tuning a new
dialog. Canonical reference dialogs: `fm/ai_config_dialog.py` (palette theming)
and `fm/form_dialog.py` (form fields).

Each entry: **symptom → cause → fix**. Code is illustrative; copy the idiom, not
the literal colors.

## Golden rules

- **Paint from the palette, not Textual `$vars`.** A dialog should implement
  `_get_palette()` + `apply_theme()` and set widget colors inline from the
  palette so a theme switch (`Ctrl+T` / Options menu) restyles it. CSS is for
  *layout only*. Pattern: copy `_get_palette()`/`apply_theme()` from
  `AiConfigDialog`. `_get_palette()` walks `self.ancestors_with_self` for a
  `.palette` attribute.
- **Inline styles (`widget.styles.x = ...`) beat CSS** and apply in all states
  (focus included) — use them for per-widget palette colors. EXCEPTION: Textual
  *component* styles (`.toggle--button`, etc.) are re-cascaded and do NOT
  reliably take inline overrides — drive those from DEFAULT_CSS instead (see
  Checkbox).
- **Verify empirically, headless.** Don't trust that CSS "looks right" — mount
  the dialog in `app.run_test()` and assert the rendered result:
  ```python
  async with app.run_test(size=(100, 40)) as pilot:
      await pilot.pause()
      d = SomeDialog(...); show_modal(app.desktop, d, size=(70, 30))
      await pilot.pause(); await pilot.pause()
      w = d.query_one(SomeWidget)
      print(w.size.height, w.styles.border, w.styles.background)
      print(w.region.x)  # left alignment
      print(w.get_component_styles("toggle--button").background)  # rendered component style
  ```
  Pause 2–3 times after `show_modal` so the post-mount CSS cascade settles
  before measuring.

## Inputs / single-line fields

**Symptom:** an `Input` renders 3 rows tall.
**Cause:** Textual `Input` has a default `border: tall` (3 cells).
**Fix:** `FormDialog Input { height: 1; border: none; padding: 0 1; }`.

**Symptom:** the *focused* field is still 3 rows / shows a blue box.
**Cause:** `Input:focus` re-adds `border: tall $border` — a plain `Input` rule
does not override the `:focus` pseudo-class.
**Fix:** add the focus variant too: `FormDialog Input:focus { border: none; }`.

**Symptom:** the field background "disappears" / washes out when focused.
**Cause:** `Input:focus` applies `background-tint: $foreground 5%`, a light
overlay over your palette background.
**Fix:** zero the tint inline in `apply_theme` (persists across focus):
```python
from textual.color import Color
widget.styles.background_tint = Color(0, 0, 0, 0)
```

## Select / combo

**Symptom:** a `Select` renders 3 rows; combo box too tall.
**Cause:** `Select` is a compound widget; the visible box is the inner
`SelectCurrent` which has `border: tall`.
**Fix:** target both:
```css
FormDialog Select { height: 1; border: none; }
FormDialog SelectCurrent { height: 1; border: none; padding: 0 1; }
FormDialog Select:focus, FormDialog SelectCurrent:focus { border: none; }
```
Use `Select.NULL` (NOT `Select.BLANK`, which is `False` in Textual 8.2.x) as the
no-selection sentinel when constructing/reading the value.

**Symptom:** the drop-down list appears BEHIND the dialog on first click, then
pops to front on the second click.
**Cause:** the `SelectOverlay` paints on the dialog's layer, which is below the
modal window. The desktop defines `layers: bg tray windows overlay`; the modal
sits on `windows`.
**Fix:** promote the overlay to the top layer:
`FormDialog SelectOverlay { layer: overlay; }`.

**Symptom:** a stray Textual border around the open drop-down.
**Cause:** `SelectOverlay` (an `OptionList`) has a `tall` border, re-added on
focus when the overlay opens.
**Fix:** `FormDialog SelectOverlay, FormDialog SelectOverlay:focus { border: none; }`.

## Checkbox

**Symptom:** the checkbox is a muddy dark/light block; hard to tell on vs off.
**Cause:** Textual `Checkbox` (a `ToggleButton`) renders a filled block whose
state reads only by *color* (`$panel` bg, `$text-success` mark), and
`BUTTON_INNER` is an `X`. Inline overriding the `toggle--button` component style
is fragile — Textual re-cascades it on every `-on` change, so the rendered box
flips color on the first toggle.
**Fix:** make the *mark itself* show the state — a classic `[ ]` / `[X]` toggle.
Subclass and override `_button`, theme via DEFAULT_CSS only:
```python
from textual.content import Content
from textual.widgets import Checkbox

class _BracketCheckbox(Checkbox):
    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        mark = self.BUTTON_INNER if self.value else " "
        return Content.assemble(("[", style), (mark, style), ("]", style))
```
```css
FormDialog Checkbox > .toggle--button { background: $surface; color: $text; }
FormDialog Checkbox.-on > .toggle--button { background: $surface; color: $text; }
```
(Note: the default `ToggleButton._button` colors the side bars `▐▌` with the
button *background* color to fake a solid block, so true empty `[ ]` brackets are
only achievable by overriding `_button`.)

**Symptom:** the label sits above the box instead of beside it.
**Fix:** pass the label to the widget — `Checkbox(label, value=...)` draws it to
the RIGHT natively. Don't emit a separate label `Static` above a checkbox field.

**Symptom:** the checkbox is indented further left-to-right than the other field
labels.
**Cause:** its own `padding: 0 1` adds a left cell on top of the container
padding. ("отступ слева", distinct from a top `margin`.)
**Fix:** `FormDialog Checkbox { padding: 0; }` to sit flush with the labels.
Confirm with `widget.region.x` equal to a `.form-label`'s `region.x`.

## TextArea / multi-line fields

**Symptom:** a Textual border around the multi-line field.
**Fix:** `FormDialog TextArea { border: none; }` + `…TextArea:focus { border: none; }`;
delineate the field by its palette background instead.

**Symptom:** the line under the cursor has a clashing highlight block.
**Cause:** `.text-area--cursor-line { background: $boost }` plus the reactive
`highlight_cursor_line = True`.
**Fix:** `widget.highlight_cursor_line = False` per TextArea.

## Buttons

**Symptom:** button text is invisible / clashes with the theme.
**Cause:** stock Textual `Button` colors don't follow the palette.
**Fix:** use the project's `ShadowButton` (`from dunders.fm.dialogs import
ShadowButton`) — bold white label on a `face_bg` you choose; handle
`on_shadow_button_pressed`. This is the established idiom (AiConfigDialog,
FindFileDialog).

## Vertical spacing between fields

**Symptom:** two blank rows between fields instead of one.
**Cause:** an always-present empty error `Static` (`height: auto`) reserves a row
in addition to the label's `margin-top`.
**Fix:** hide the error row until needed —
`FormDialog .form-error { display: none; }` and flip it on only when there's a
message:
```python
err.update(msg)
err.display = bool(msg)
```

## Maintenance cautions

- **Never `rm -rf examples`** (or any repo dir) to clear a stray file — that
  directory holds *tracked* content (`examples/markdown/…`). Remove the specific
  untracked file only (`rm -f examples/<stray>.json`). If you do clobber tracked
  files, restore with `git checkout -- <paths>`.
- Interactive/diagnostic runs that open a form via the seed path can leave
  `examples/*.form.json` / `*.result.json` artifacts in the repo root. They are
  NOT produced by the test suite — delete the specific files and move on.
- Clean up throwaway `/tmp/*.py` diagnostic scripts when done.
