# File Panel Mouse-Wheel Cursor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mouse wheel move the file-panel cursor (±3 entries per notch, like ↑/↓), activating the panel first when it scrolls over the inactive one.

**Architecture:** Override Textual's system-dispatch `_on_mouse_scroll_down`/`_on_mouse_scroll_up` on `FilePanel` (the base `Widget._on_mouse_scroll_*` no-ops on a non-scrollable widget and lets the event bubble — see the `_BufferView` precedent). Both delegate to a shared `_wheel(delta)` helper that activates the panel if inactive (posting `Window.FocusRequested`, the same path a click uses), then moves the cursor via the existing `move_cursor`.

**Tech Stack:** Python ≥3.12, Textual, pytest (asyncio auto-mode).

---

## Background for the implementer (read once)

File under change: `tyui/fm/file_panel.py` — the `FilePanel(WindowContent)` widget.

- `FilePanel(cwd=...)` can be constructed standalone; `refresh_listing()` loads
  `self.entries`. Most existing tests in `tests/fm/test_file_panel.py` use the
  panel **unmounted** (no app), calling methods like `move_cursor` directly.
- `move_cursor(delta)` clamps `self.cursor` to `[0, len(entries)-1]` and calls
  `_ensure_cursor_visible()`. `self.cursor` starts at 0.
- `_qs_reset()` ends quick-search; the keyboard cursor handlers call it before moving.
- `_is_active_panel` (property) is True when the panel `has_focus` OR its enclosing
  windowing `Window` is the `Desktop.focused_window`.
- Clicking activates a window through `Window.on_mouse_down` → `Window.FocusRequested`.
  The wheel emits `MouseScrollUp/Down`, not `MouseDown`, so it must request
  activation explicitly.
- Precedent (copy the shape exactly), `tyui/fm/console/window.py`:
  ```python
  def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
      self._scroll_view(+3)
      event.stop()
      event.prevent_default()
  def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
      self._scroll_view(-3)
      event.stop()
      event.prevent_default()
  ```
  Scroll **down** → move toward the bottom (cursor +3); scroll **up** → cursor −3.

Run panel tests: `cd /Users/tumi/prj_python/tyui && pytest tests/fm/test_file_panel.py -v`
Lint: `ruff check tyui/fm/file_panel.py`

`events` is already imported in `file_panel.py` (`from textual import events`).

---

## File Structure

- Modify: `tyui/fm/file_panel.py` — add `_WHEEL_STEP`, `_enclosing_window()`,
  `_wheel()`, `_on_mouse_scroll_down/up`; refactor `_is_active_panel` to use the
  helper.
- Test: `tests/fm/test_file_panel.py` — standalone wheel tests.
- Test: `tests/fm/test_we_mc_mode.py` — one async activation test (this file
  already has the app + `import pytest` + `TyuiApp` harness).

---

## Task 1: Extract `_enclosing_window()` and refactor `_is_active_panel`

**Files:**
- Modify: `tyui/fm/file_panel.py` (`_is_active_panel`, new `_enclosing_window`)
- Test: `tests/fm/test_file_panel.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/fm/test_file_panel.py`:

```python
def test_enclosing_window_is_none_when_unparented(tmp_path: Path):
    # A standalone (unmounted) panel has no enclosing windowing Window.
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p._enclosing_window() is None
    # And it is therefore not the active panel.
    assert p._is_active_panel is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/fm/test_file_panel.py::test_enclosing_window_is_none_when_unparented -v`
Expected: FAIL — `AttributeError: 'FilePanel' object has no attribute '_enclosing_window'`.

- [ ] **Step 3: Add the helper and refactor**

In `tyui/fm/file_panel.py`, find the `_is_active_panel` property. Replace the
whole property with the version below AND add the new `_enclosing_window` method
just above it:

```python
    def _enclosing_window(self):
        """Walk up to the enclosing windowing ``Window``, or ``None`` when the
        panel is not mounted under one (e.g. standalone in unit tests)."""
        from tyui.windowing.window import Window

        node = self.parent
        while node is not None and not isinstance(node, Window):
            node = getattr(node, "parent", None)
        return node  # a Window or None

    @property
    def _is_active_panel(self) -> bool:
        """True when this panel is the "active" one for rendering purposes.

        A panel is active when it has Textual widget focus OR when it is
        the content of the Desktop's focused_window (i.e. it is the
        logical active panel even when Textual widget focus is elsewhere,
        such as on the CommandLine input).
        """
        if self.has_focus:
            return True
        try:
            from tyui.windowing.desktop import Desktop

            win = self._enclosing_window()
            if win is None:
                return False
            node = win.parent
            while node is not None and not isinstance(node, Desktop):
                node = getattr(node, "parent", None)
            if node is None:
                return False
            return node.focused_window is win
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/test_file_panel.py -v`
Expected: PASS (the new test plus all existing panel tests — the refactor is
behaviour-preserving).

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/file_panel.py tests/fm/test_file_panel.py
git commit -m "refactor(file-panel): extract _enclosing_window() helper"
```

---

## Task 2: Wheel moves the cursor (`_wheel` + scroll handlers)

**Files:**
- Modify: `tyui/fm/file_panel.py` (module constant `_WHEEL_STEP`, `_wheel`,
  `_on_mouse_scroll_down`, `_on_mouse_scroll_up`)
- Test: `tests/fm/test_file_panel.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/fm/test_file_panel.py`:

```python
class _FakeScroll:
    """Duck-typed stand-in for a Textual MouseScroll event."""

    def __init__(self) -> None:
        self.stopped = False
        self.prevented = False

    def stop(self) -> None:
        self.stopped = True

    def prevent_default(self) -> None:
        self.prevented = True


def test_wheel_moves_cursor_by_step(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    assert p.cursor == 0
    p._wheel(3)
    assert p.cursor == 3
    p._wheel(-3)
    assert p.cursor == 0


def test_wheel_clamps_at_bounds(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    last = len(p.entries) - 1
    p._wheel(-3)               # already at top
    assert p.cursor == 0
    p.end()                    # jump to bottom
    p._wheel(3)                # past the end
    assert p.cursor == last


def test_wheel_on_minimal_listing_does_not_crash(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    p = FilePanel(cwd=sub)     # only the synthetic ".." row
    p.refresh_listing()
    p._wheel(3)
    p._wheel(-3)
    assert p.cursor == 0


def test_scroll_down_handler_moves_cursor_and_stops_event(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    ev = _FakeScroll()
    p._on_mouse_scroll_down(ev)
    assert p.cursor == 3
    assert ev.stopped and ev.prevented


def test_scroll_up_handler_moves_cursor_and_stops_event(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    p = FilePanel(cwd=tmp_path)
    p.refresh_listing()
    p.end()
    last = p.cursor
    ev = _FakeScroll()
    p._on_mouse_scroll_up(ev)
    assert p.cursor == last - 3
    assert ev.stopped and ev.prevented
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/fm/test_file_panel.py -k "wheel or scroll" -v`
Expected: FAIL — `AttributeError: 'FilePanel' object has no attribute '_wheel'`.

- [ ] **Step 3: Implement the constant, helper, and handlers**

In `tyui/fm/file_panel.py`, add the module-level constant just after the imports
(near `__all__`):

```python
# Entries the cursor moves per mouse-wheel notch (matches typical 3-line scroll).
_WHEEL_STEP = 3
```

Then add these methods to `FilePanel`, right after `_enclosing_window`
(added in Task 1):

```python
    def _wheel(self, delta: int) -> None:
        """Move the cursor by ``delta`` entries in response to a wheel notch.

        If the panel is not the active one, request focus on its window first
        (the same path a click takes) so the wheel both activates and scrolls,
        matching Midnight Commander. Clamping and viewport follow are handled by
        ``move_cursor`` / ``_ensure_cursor_visible``.
        """
        if not self._is_active_panel:
            win = self._enclosing_window()
            if win is not None:
                from tyui.windowing.window import Window

                self.post_message(Window.FocusRequested(win))
        self._qs_reset()
        self.move_cursor(delta)
        self.refresh()

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self._wheel(_WHEEL_STEP)
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self._wheel(-_WHEEL_STEP)
        event.stop()
        event.prevent_default()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/fm/test_file_panel.py -k "wheel or scroll" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tyui/fm/file_panel.py tests/fm/test_file_panel.py
git commit -m "feat(file-panel): mouse wheel moves the cursor (±3 per notch)"
```

---

## Task 3: Activation on scroll over the inactive panel (integration)

**Files:**
- Test: `tests/fm/test_we_mc_mode.py` (app-mounted async test)

- [ ] **Step 1: Write the test**

Add to `tests/fm/test_we_mc_mode.py` (it already has `import pytest` and
`from tyui.app import TyuiApp`):

```python
@pytest.mark.asyncio
async def test_wheel_over_inactive_panel_activates_and_scrolls(tmp_path):
    from tyui.fm.file_panel import FilePanel

    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    app = TyuiApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        panels = list(app.query(FilePanel))
        inactive = next(p for p in panels if not p._is_active_panel)
        # Point the inactive panel at the populated dir so the move is
        # deterministic regardless of where it seeded.
        inactive.cwd = tmp_path
        inactive.refresh_listing()
        before = inactive.cursor
        inactive._wheel(3)
        await pilot.pause()
        assert inactive._is_active_panel  # wheel activated it
        assert inactive.cursor == min(before + 3, len(inactive.entries) - 1)
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/fm/test_we_mc_mode.py::test_wheel_over_inactive_panel_activates_and_scrolls -v`
Expected: PASS — `_wheel` posts `Window.FocusRequested`, which (after
`pilot.pause()`) makes the panel's window the `Desktop.focused_window`, so
`_is_active_panel` is True and the cursor moved by 3.

(If it FAILS on activation, the `Window.FocusRequested` wiring in `_wheel` is
wrong — fix `_wheel` in `file_panel.py`, do not weaken the test.)

- [ ] **Step 3: Commit**

```bash
git add tests/fm/test_we_mc_mode.py
git commit -m "test(file-panel): wheel over inactive panel activates it"
```

---

## Task 4: Finalize — full suite, lint, manual check

**Files:** none (verification).

- [ ] **Step 1: Run the file-manager test suites**

Run: `pytest tests/fm/ -q`
Expected: all pass, no regressions.

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 3: Lint**

Run: `ruff check tyui/fm/file_panel.py`
Expected: clean.

- [ ] **Step 4: Manual smoke (optional, real terminal)**

Run `tyui` in a real terminal. Wheel-scroll over the file list: the cursor moves
~3 rows per notch and the listing follows. Wheel over the inactive (other) panel:
it becomes active, then scrolls.

---

## Self-review notes

- **Spec coverage:** `_on_mouse_scroll_down/up` overrides + `_wheel` ↔ Solution;
  `_WHEEL_STEP = 3` ↔ step decision; activation via `Window.FocusRequested` ↔
  inactive-panel decision; `_qs_reset` ↔ "match keyboard handlers";
  `_enclosing_window` extraction ↔ Refactor; tests (move/clamp/minimal/handler-stop
  /activation) ↔ Testing section.
- **Placeholder scan:** none — every code step has complete code.
- **Type consistency:** `_wheel(delta: int)`, `_enclosing_window() -> Window|None`,
  `_WHEEL_STEP`, `_on_mouse_scroll_down/up` are used identically across tasks; the
  `_FakeScroll` stub provides the `stop`/`prevent_default` the handlers call.
- **Note:** `refresh()` and `post_message()` on an unmounted panel are safe no-ops
  in Textual, so the standalone Task 2 tests (which never mount) run `_wheel`
  without an app; the activation path (which needs a mounted window) is covered by
  the async Task 3 test.
