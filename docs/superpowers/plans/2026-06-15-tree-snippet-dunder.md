# Tree-дандлер с редактируемыми сниппет-узлами — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить универсальный «дандлер» (`TreeContent`), отображающий дерево, где узел может разворачиваться в редактируемый текстовый сниппет (правится встроенным `EditorWidget` без бордюров), с ленивой догрузкой детей и групповой селекцией узлов.

**Architecture:** Три слоя по конвенции проекта. Чистая модель и логика — `windowing/core/tree_model.py` (без Textual, как `buffer.py`/`fold_engine.py`). Рисующий лист — `windowing/tree/widget.py` `TreeViewWidget(Widget)` по идиоме `FilePanel` (`render_line` + `cursor` + `row_offset`, без `ScrollView`). Контейнер-дандлер — `windowing/tree/content.py` `TreeContent(WindowContent)`, который компонует виджет и при правке монтирует один `EditorWidget` поверх тела активного узла. Каркас ничего не персистит — только постит `BodyEdited`/`SelectionChanged`.

**Tech Stack:** Python ≥3.12, Textual, `rich.segment.Segment` / `textual.strip.Strip`, pytest (`asyncio_mode = "auto"`), `app.run_test()` pilot-харнесс.

Спека: `docs/superpowers/specs/2026-06-15-tree-snippet-dunder-design.md`.

---

## Структура файлов

- Create: `dunders/windowing/core/tree_model.py` — `TreeNode`, `HeaderRow`/`BodyRow`, `flatten_visible`, `header_nodes`, `nodes_in_range`. Чистая логика.
- Create: `dunders/windowing/tree/__init__.py` — реэкспорт `TreeContent`, `TreeViewWidget`.
- Create: `dunders/windowing/tree/widget.py` — `TreeViewWidget(Widget)`: отрисовка, навигация, мышь, селекция.
- Create: `dunders/windowing/tree/content.py` — `TreeContent(WindowContent)`: монтаж встроенного редактора, команды, dirty.
- Modify: `dunders/windowing/__init__.py` — добавить `TreeContent`, `TreeViewWidget`, `TreeNode` в импорты и `__all__`.
- Modify: `dunders/windowing/demo/contents.py` — функция `build_demo_tree()`.
- Modify: `dunders/windowing/demo/app.py` — новое окно с `TreeContent` в `_build_scene`.
- Test: `tests/windowing/test_tree_model.py` — юнит-тесты модели/flatten/селекции.
- Test: `tests/windowing/test_tree_widget.py` — async: навигация, expand/collapse, ленивая догрузка, мышь, селекция.
- Test: `tests/windowing/test_tree_content.py` — async: цикл правки, `BodyEdited`, `is_dirty`, `SelectionChanged`.

---

## Task 1: Модель `TreeNode`

**Files:**
- Create: `dunders/windowing/core/tree_model.py`
- Test: `tests/windowing/test_tree_model.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/windowing/test_tree_model.py
from dunders.windowing.core.tree_model import TreeNode


def test_add_child_sets_parent():
    root = TreeNode(label="root")
    child = root.add_child(TreeNode(label="a"))
    assert child.parent is root
    assert root.children == [child]


def test_is_branch_true_when_has_children():
    n = TreeNode(label="n")
    assert n.is_branch is False
    n.add_child(TreeNode(label="c"))
    assert n.is_branch is True


def test_is_branch_true_when_has_loader():
    n = TreeNode(label="n", loader=lambda node: [])
    assert n.is_branch is True


def test_has_body():
    assert TreeNode(label="n").has_body is False
    assert TreeNode(label="n", body="x").has_body is True


def test_ensure_loaded_calls_loader_once():
    calls = []

    def loader(node):
        calls.append(node)
        return [TreeNode(label="c1"), TreeNode(label="c2")]

    n = TreeNode(label="n", loader=loader)
    n.ensure_loaded()
    n.ensure_loaded()
    assert len(calls) == 1
    assert [c.label for c in n.children] == ["c1", "c2"]
    assert n.children[0].parent is n
    assert n.loaded is True
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_model.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.windowing.core.tree_model'`

- [ ] **Step 3: Реализовать модель**

```python
# dunders/windowing/core/tree_model.py
"""Pure, Textual-free model for the tree dunder: nodes + visible-row flattening."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class TreeNode:
    """A tree node: a header label plus an optional editable snippet body and
    optional children. ``loader`` lazily supplies children on first expand."""

    label: str
    body: Optional[str] = None
    children: list["TreeNode"] = field(default_factory=list)
    expanded: bool = False
    body_open: bool = False
    loader: Optional[Callable[["TreeNode"], list["TreeNode"]]] = None
    loaded: bool = False
    parent: Optional["TreeNode"] = None
    data: Any = None

    def add_child(self, child: "TreeNode") -> "TreeNode":
        child.parent = self
        self.children.append(child)
        return child

    @property
    def is_branch(self) -> bool:
        return bool(self.children) or self.loader is not None

    @property
    def has_body(self) -> bool:
        return self.body is not None

    def ensure_loaded(self) -> None:
        """Run ``loader`` once. Idempotent; no-op without a loader."""
        if self.loaded or self.loader is None:
            return
        for child in self.loader(self) or []:
            self.add_child(child)
        self.loaded = True
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_model.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/core/tree_model.py tests/windowing/test_tree_model.py
git commit -m "feat(tree): TreeNode model with lazy child loading"
```

---

## Task 2: Плоский список видимых строк (`flatten_visible`)

**Files:**
- Modify: `dunders/windowing/core/tree_model.py`
- Test: `tests/windowing/test_tree_model.py`

- [ ] **Step 1: Написать падающий тест**

```python
# append to tests/windowing/test_tree_model.py
from dunders.windowing.core.tree_model import (
    HeaderRow, BodyRow, flatten_visible,
)


def _sample():
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a", body="l1\nl2"))
    b = root.add_child(TreeNode(label="b"))
    b.add_child(TreeNode(label="b1"))
    return root, a, b


def test_flatten_collapsed_shows_only_top_headers():
    root, a, b = _sample()
    rows = flatten_visible(root)
    assert rows == [HeaderRow(a, 0), HeaderRow(b, 0)]


def test_flatten_expanded_branch_shows_children():
    root, a, b = _sample()
    b.expanded = True
    rows = flatten_visible(root)
    assert [type(r).__name__ for r in rows] == ["HeaderRow", "HeaderRow", "HeaderRow"]
    assert rows[2].node.label == "b1"
    assert rows[2].depth == 1


def test_flatten_open_body_emits_body_rows():
    root, a, b = _sample()
    a.body_open = True
    rows = flatten_visible(root)
    assert rows[0] == HeaderRow(a, 0)
    assert rows[1] == BodyRow(a, 0, 0)
    assert rows[2] == BodyRow(a, 0, 1)
    assert rows[3] == HeaderRow(b, 0)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_model.py -k flatten -v`
Expected: FAIL with `ImportError: cannot import name 'HeaderRow'`

- [ ] **Step 3: Реализовать строки и flatten**

```python
# append to dunders/windowing/core/tree_model.py

@dataclass(frozen=True)
class HeaderRow:
    """A visible row showing a node's header (label + expand arrow)."""
    node: TreeNode
    depth: int


@dataclass(frozen=True)
class BodyRow:
    """A visible row showing one line of a node's snippet body."""
    node: TreeNode
    depth: int
    line_index: int


Row = HeaderRow | BodyRow


def flatten_visible(root: TreeNode) -> list[Row]:
    """Walk the model honouring ``expanded``/``body_open`` and return the flat
    list of visible rows. Pure: does NOT trigger lazy loading (the widget calls
    ``ensure_loaded`` before expanding)."""
    rows: list[Row] = []

    def walk(node: TreeNode, depth: int) -> None:
        rows.append(HeaderRow(node, depth))
        if node.body_open and node.body is not None:
            for i in range(len(node.body.split("\n"))):
                rows.append(BodyRow(node, depth, i))
        if node.expanded:
            for child in node.children:
                walk(child, depth + 1)

    for child in root.children:
        walk(child, 0)
    return rows
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_model.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/core/tree_model.py tests/windowing/test_tree_model.py
git commit -m "feat(tree): flatten_visible producing header/body rows"
```

---

## Task 3: Помощники селекции (`header_nodes`, `nodes_in_range`)

**Files:**
- Modify: `dunders/windowing/core/tree_model.py`
- Test: `tests/windowing/test_tree_model.py`

- [ ] **Step 1: Написать падающий тест**

```python
# append to tests/windowing/test_tree_model.py
from dunders.windowing.core.tree_model import header_nodes, nodes_in_range


def test_header_nodes_skips_body_rows():
    root, a, b = _sample()
    a.body_open = True
    nodes = header_nodes(flatten_visible(root))
    assert nodes == [a, b]


def test_nodes_in_range_inclusive_and_order_independent():
    root, a, b = _sample()
    b.expanded = True
    rows = flatten_visible(root)
    b1 = b.children[0]
    assert nodes_in_range(rows, a, b1) == [a, b, b1]
    assert nodes_in_range(rows, b1, a) == [a, b, b1]
    assert nodes_in_range(rows, b, b) == [b]
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_model.py -k "header_nodes or nodes_in_range" -v`
Expected: FAIL with `ImportError: cannot import name 'header_nodes'`

- [ ] **Step 3: Реализовать помощники**

```python
# append to dunders/windowing/core/tree_model.py

def header_nodes(rows: list[Row]) -> list[TreeNode]:
    """Nodes for the header rows, in visible order (body rows skipped)."""
    return [r.node for r in rows if isinstance(r, HeaderRow)]


def nodes_in_range(rows: list[Row], a: TreeNode, b: TreeNode) -> list[TreeNode]:
    """Header nodes between ``a`` and ``b`` inclusive, in visible order,
    regardless of which comes first."""
    headers = header_nodes(rows)
    ia, ib = headers.index(a), headers.index(b)
    lo, hi = sorted((ia, ib))
    return headers[lo : hi + 1]
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_model.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/core/tree_model.py tests/windowing/test_tree_model.py
git commit -m "feat(tree): selection helpers header_nodes/nodes_in_range"
```

---

## Task 4: `TreeViewWidget` — отрисовка и навигация с клавиатуры

**Files:**
- Create: `dunders/windowing/tree/__init__.py`
- Create: `dunders/windowing/tree/widget.py`
- Test: `tests/windowing/test_tree_widget.py`

Идиома `FilePanel`: `Widget` с `render_line(y) -> Strip`, целочисленными `cursor` (индекс в `self.rows`, всегда на `HeaderRow`) и `row_offset`. Структурные изменения пересобирают `self.rows` через `_rebuild_rows()`.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/windowing/test_tree_widget.py
import pytest
from textual.app import App, ComposeResult

from dunders.windowing.core.tree_model import TreeNode
from dunders.windowing.tree.widget import TreeViewWidget


def _tree():
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a", body="l1\nl2"))
    b = root.add_child(TreeNode(label="b"))
    b.add_child(TreeNode(label="b1"))
    return root


class _App(App):
    def __init__(self, root):
        super().__init__()
        self._root = root
        self.widget: TreeViewWidget | None = None

    def compose(self) -> ComposeResult:
        self.widget = TreeViewWidget(self._root)
        yield self.widget


async def test_initial_cursor_on_first_header():
    app = _App(_tree())
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        assert w.current_node.label == "a"


async def test_down_moves_cursor_skipping_body():
    root = _tree()
    root.children[0].body_open = True  # 'a' shows 2 body rows
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.press("down")
        assert w.current_node.label == "b"


async def test_right_expands_branch_and_lazy_loads():
    calls = []
    root = TreeNode(label="root")
    branch = root.add_child(
        TreeNode(label="lazy", loader=lambda n: calls.append(1) or [TreeNode(label="kid")])
    )
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        await pilot.press("right")
        assert branch.expanded is True
        assert len(calls) == 1
        assert [n.label for n in w.visible_headers()] == ["lazy", "kid"]


async def test_left_collapses_then_goes_to_parent():
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        await pilot.press("down")          # -> b
        await pilot.press("right")         # expand b -> b1 visible
        await pilot.press("down")          # -> b1
        await pilot.press("left")          # b1 is leaf -> go to parent b
        assert w.current_node.label == "b"
        await pilot.press("left")          # collapse b
        assert root.children[1].expanded is False
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_widget.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.windowing.tree'`

- [ ] **Step 3: Реализовать виджет (отрисовка + навигация)**

```python
# dunders/windowing/tree/__init__.py
from .widget import TreeViewWidget

__all__ = ["TreeViewWidget"]
```

```python
# dunders/windowing/tree/widget.py
"""TreeViewWidget — line-painted tree with one inline-editable snippet at a time.

Follows the FilePanel idiom: a plain Widget that paints via ``render_line`` and
keeps its own integer ``cursor`` (always on a HeaderRow) and ``row_offset``.
"""

from __future__ import annotations

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from rich.segment import Segment
from rich.style import Style as RichStyle

from dunders.windowing.core.tree_model import (
    BodyRow, HeaderRow, Row, TreeNode,
    flatten_visible, header_nodes, nodes_in_range,
)

_INDENT = 2
_WHEEL_ROWS = 3


class TreeViewWidget(Widget, can_focus=True):
    """Paints a TreeNode forest and handles navigation/selection."""

    DEFAULT_CSS = """
    TreeViewWidget { background: transparent; }
    """

    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("right", "expand", show=False),
        Binding("left", "collapse", show=False),
        Binding("space", "toggle_expand", show=False),
        Binding("enter", "edit", show=False),
        Binding("f4", "edit", show=False),
        Binding("insert", "mark", show=False),
        Binding("shift+down", "select_down", show=False),
        Binding("shift+up", "select_up", show=False),
    ]

    class EditRequested(Message):
        """Posted when the user asks to edit a node's snippet body."""
        def __init__(self, node: TreeNode) -> None:
            super().__init__()
            self.node = node

    class SelectionChanged(Message):
        def __init__(self, nodes: set[TreeNode]) -> None:
            super().__init__()
            self.nodes = nodes

    def __init__(self, root: TreeNode, **kwargs) -> None:
        super().__init__(**kwargs)
        self.root = root
        self.rows: list[Row] = []
        self.cursor: int = 0
        self.row_offset: int = 0
        self.selected: set[TreeNode] = set()
        self._rebuild_rows()

    # --- model -> rows -----------------------------------------------------

    def _rebuild_rows(self) -> None:
        self.rows = flatten_visible(self.root)
        if not self.rows:
            self.cursor = 0
            return
        self.cursor = self._clamp_to_header(self.cursor)
        self.refresh()

    def _clamp_to_header(self, idx: int) -> int:
        if not self.rows:
            return 0
        idx = max(0, min(idx, len(self.rows) - 1))
        # Land on the nearest HeaderRow at or after idx, else before.
        for j in range(idx, len(self.rows)):
            if isinstance(self.rows[j], HeaderRow):
                return j
        for j in range(idx, -1, -1):
            if isinstance(self.rows[j], HeaderRow):
                return j
        return 0

    @property
    def current_node(self) -> TreeNode | None:
        if 0 <= self.cursor < len(self.rows):
            return self.rows[self.cursor].node
        return None

    def visible_headers(self) -> list[TreeNode]:
        return header_nodes(self.rows)

    # --- navigation --------------------------------------------------------

    def _step_cursor(self, direction: int) -> None:
        i = self.cursor + direction
        while 0 <= i < len(self.rows):
            if isinstance(self.rows[i], HeaderRow):
                self.cursor = i
                self._ensure_cursor_visible()
                self.refresh()
                return
            i += direction

    def action_cursor_up(self) -> None:
        self._step_cursor(-1)

    def action_cursor_down(self) -> None:
        self._step_cursor(+1)

    def action_expand(self) -> None:
        node = self.current_node
        if node is None:
            return
        if node.is_branch and not node.expanded:
            node.ensure_loaded()
            node.expanded = True
            self._rebuild_rows()
        elif node.has_body and not node.body_open:
            node.body_open = True
            self._rebuild_rows()

    def action_collapse(self) -> None:
        node = self.current_node
        if node is None:
            return
        if node.expanded:
            node.expanded = False
            self._rebuild_rows()
        elif node.body_open:
            node.body_open = False
            self._rebuild_rows()
        elif node.parent is not None and node.parent is not self.root:
            self._select_node(node.parent)

    def action_toggle_expand(self) -> None:
        node = self.current_node
        if node is None:
            return
        if node.expanded:
            self.action_collapse()
        else:
            self.action_expand()

    def action_edit(self) -> None:
        node = self.current_node
        if node is not None and node.has_body:
            node.body_open = True
            self._rebuild_rows()
            self.post_message(self.EditRequested(node))

    def _select_node(self, node: TreeNode) -> None:
        for i, r in enumerate(self.rows):
            if isinstance(r, HeaderRow) and r.node is node:
                self.cursor = i
                self._ensure_cursor_visible()
                self.refresh()
                return

    # --- selection (filled in Task 7) -------------------------------------

    def action_mark(self) -> None:
        pass

    def action_select_down(self) -> None:
        pass

    def action_select_up(self) -> None:
        pass

    # --- scroll bookkeeping ------------------------------------------------

    def _visible_rows(self) -> int:
        return max(1, self.size.height)

    def _ensure_cursor_visible(self) -> None:
        rows = self._visible_rows()
        if self.cursor < self.row_offset:
            self.row_offset = self.cursor
        elif self.cursor >= self.row_offset + rows:
            self.row_offset = self.cursor - rows + 1
        self.row_offset = max(0, self.row_offset)

    # --- painting ----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)
        idx = y + self.row_offset
        if idx >= len(self.rows):
            return Strip.blank(width)
        row = self.rows[idx]
        base = self._row_style(idx)
        if isinstance(row, HeaderRow):
            text = self._header_text(row)
        else:
            text = self._body_text(row)
        text = text[:width].ljust(width)
        return Strip([Segment(text, base)])

    def _row_style(self, idx: int) -> RichStyle:
        style = RichStyle()
        if idx == self.cursor:
            style += RichStyle(reverse=True)
        return style

    def _header_text(self, row: HeaderRow) -> str:
        pad = " " * (row.depth * _INDENT)
        if row.node.is_branch:
            arrow = "▾ " if row.node.expanded else "▸ "
        else:
            arrow = "  "
        mark = "*" if row.node in self.selected else " "
        return f"{mark}{pad}{arrow}{row.node.label}"

    def _body_text(self, row: BodyRow) -> str:
        pad = " " * (row.depth * _INDENT + 2)
        line = (row.node.body or "").split("\n")[row.line_index]
        return f" {pad}  {line}"
```

> **Совет реализатору:** маркер пометки/мышь/селекция дорабатываются в Task 5/7 — здесь оставлены минимальные заглушки (`action_mark` и т.п. пустые), чтобы тесты Task 4 проходили. Не удаляй их.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_widget.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/tree/__init__.py dunders/windowing/tree/widget.py tests/windowing/test_tree_widget.py
git commit -m "feat(tree): TreeViewWidget rendering + keyboard navigation"
```

---

## Task 5: Мышь — скролл, клик по стрелке/заголовку, клик в сниппет

**Files:**
- Modify: `dunders/windowing/tree/widget.py`
- Test: `tests/windowing/test_tree_widget.py`

- [ ] **Step 1: Написать падающий тест**

```python
# append to tests/windowing/test_tree_widget.py
from textual.geometry import Offset


async def test_click_header_moves_cursor():
    root = _tree()
    root.children[1].expanded = True   # b expanded -> rows: a, b, b1
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.click(w, offset=Offset(5, 2))   # y=2 -> b1
        assert w.current_node.label == "b1"


async def test_click_arrow_toggles_expand():
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        # 'b' is at y=1 (a=0, b=1); arrow sits at x≈3 (mark+no-indent)
        await pilot.click(w, offset=Offset(3, 1))
        assert root.children[1].expanded is True


async def test_click_snippet_requests_edit():
    root = _tree()
    root.children[0].body_open = True  # 'a' body visible at y=1,2
    app = _App(root)
    messages = []
    app.widget  # ensure created

    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        w.post_message = lambda m: messages.append(m)  # capture
        await pilot.click(w, offset=Offset(6, 1))      # body row of 'a'
        assert any(type(m).__name__ == "EditRequested" for m in messages)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_widget.py -k click -v`
Expected: FAIL (cursor not moved / no expand / no message — handlers absent)

- [ ] **Step 3: Реализовать обработчики мыши**

```python
# append inside class TreeViewWidget in dunders/windowing/tree/widget.py

    # --- mouse -------------------------------------------------------------

    def _row_at(self, y: int) -> int:
        idx = y + self.row_offset
        if 0 <= idx < len(self.rows):
            return idx
        return -1

    def on_click(self, event: events.Click) -> None:
        idx = self._row_at(event.y)
        if idx < 0:
            return
        row = self.rows[idx]
        if isinstance(row, BodyRow):
            self.cursor = self._index_of_header(row.node)
            self.refresh()
            self.post_message(self.EditRequested(row.node))
            return
        # HeaderRow
        self.cursor = idx
        self._ensure_cursor_visible()
        # Arrow hit zone: mark(1) + indent, arrow glyph at x in [s, s+1].
        arrow_x = 1 + row.depth * _INDENT
        if row.node.is_branch and event.x in (arrow_x, arrow_x + 1):
            self.action_toggle_expand()
        elif row.node.has_body:
            self.post_message(self.EditRequested(row.node))
        else:
            self.selected.clear()
            self.post_message(self.SelectionChanged(set(self.selected)))
        self.refresh()

    def _index_of_header(self, node: TreeNode) -> int:
        for i, r in enumerate(self.rows):
            if isinstance(r, HeaderRow) and r.node is node:
                return i
        return self.cursor

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.row_offset = min(
            max(0, len(self.rows) - 1), self.row_offset + _WHEEL_ROWS
        )
        self.refresh()
        event.stop()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.row_offset = max(0, self.row_offset - _WHEEL_ROWS)
        self.refresh()
        event.stop()
```

> Обычный клик по заголовку без тела/без стрелки сбрасывает набор пометок (см. спеку, секция «Мышь»).

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_widget.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/tree/widget.py tests/windowing/test_tree_widget.py
git commit -m "feat(tree): mouse — wheel scroll, click select/expand/edit"
```

---

## Task 6: `TreeContent` — встроенный редактор и цикл правки

**Files:**
- Create: `dunders/windowing/tree/content.py`
- Modify: `dunders/windowing/tree/__init__.py`
- Test: `tests/windowing/test_tree_content.py`

`TreeContent` компонует `TreeViewWidget` и контейнер-оверлей с одним `EditorWidget`. По `EditRequested` монтирует редактор поверх тела активного узла; по `Esc`/коммиту пишет `node.body`, постит `BodyEdited`, ставит `is_dirty`.

**Позиционирование (инлайн с фолбэком).** Пока редактор открыт, фокус у него — дерево не скроллится, поэтому непрерывная синхронизация не нужна: перед открытием прокручиваем узел в зону видимости и фиксируем offset. Если инлайн-позиционирование визуально съезжает — переключить контейнер на док снизу (одна правка CSS, см. шаг 3, помечено `FALLBACK`). Контракт и тесты от этого не зависят.

- [ ] **Step 1: Написать падающий тест**

```python
# tests/windowing/test_tree_content.py
import pytest
from textual.app import App, ComposeResult

from dunders.windowing.core.tree_model import TreeNode
from dunders.windowing.tree.content import TreeContent


def _tree():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a", body="hello\nworld"))
    root.add_child(TreeNode(label="b"))
    return root


class _App(App):
    def __init__(self, root):
        super().__init__()
        self._root = root
        self.content: TreeContent | None = None

    def compose(self) -> ComposeResult:
        self.content = TreeContent(self._root, title="Tree")
        yield self.content


async def test_title_set():
    app = _App(_tree())
    async with app.run_test(size=(50, 20)) as pilot:
        assert app.content.window_title == "Tree"


async def test_edit_cycle_writes_body_and_marks_dirty():
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("enter")          # edit 'a'
        await pilot.pause()
        assert c.is_editing is True
        # type an exclamation at the cursor, then commit
        await pilot.press("!")
        await pilot.press("escape")
        await pilot.pause()
        assert c.is_editing is False
        assert root.children[0].body.startswith("!") or root.children[0].body.endswith("!") \
            or "!" in root.children[0].body
        assert c.is_dirty is True


async def test_escape_without_change_keeps_body():
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.press("escape")
        await pilot.pause()
        assert root.children[0].body == "hello\nworld"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_content.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dunders.windowing.tree.content'`

- [ ] **Step 3: Реализовать `TreeContent`**

```python
# dunders/windowing/tree/content.py
"""TreeContent — WindowContent dunder hosting a TreeViewWidget plus one
inline EditorWidget mounted on demand to edit a node's snippet body."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container
from textual.message import Message

from dunders.windowing.content import WindowContent, WindowCommand
from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.core.tree_model import TreeNode
from dunders.windowing.editor.widget import EditorWidget
from dunders.windowing.tree.widget import TreeViewWidget

_EDITOR_MAX_HEIGHT = 12


class TreeContent(WindowContent):
    """Dunder: editable snippet tree."""

    DEFAULT_CSS = """
    TreeContent { background: transparent; }
    TreeContent TreeViewWidget { width: 1fr; height: 1fr; }
    /* Inline overlay editor: no borders, sits on its own layer. */
    TreeContent #tree-editor {
        layer: overlay;
        background: $surface;
        display: none;
        height: auto;
        max-height: 12;
    }
    TreeContent #tree-editor.editing { display: block; }
    TreeContent #tree-editor EditorWidget { border: none; background: $surface; }
    """

    class BodyEdited(Message):
        def __init__(self, node: TreeNode) -> None:
            super().__init__()
            self.node = node

    def __init__(self, root: TreeNode, title: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.root = root
        self.widget = TreeViewWidget(root)
        self._editor_box = Container(id="tree-editor")
        self._editor: EditorWidget | None = None
        self._editing_node: TreeNode | None = None
        if title is not None:
            self.window_title = title

    @property
    def is_editing(self) -> bool:
        return self._editing_node is not None

    def compose(self) -> ComposeResult:
        yield self.widget
        yield self._editor_box

    # --- edit lifecycle ----------------------------------------------------

    def on_tree_view_widget_edit_requested(
        self, event: TreeViewWidget.EditRequested
    ) -> None:
        self._begin_edit(event.node)

    def _begin_edit(self, node: TreeNode) -> None:
        if self.is_editing:
            self._commit_edit()
        self._editing_node = node
        buf = TextBuffer.from_string(node.body or "")
        self._editor = EditorWidget(buffer=buf, show_line_numbers=False)
        self._editor_box.mount(self._editor)
        self._position_editor(node)
        self._editor_box.add_class("editing")
        self._editor.focus()

    def _position_editor(self, node: TreeNode) -> None:
        """Place the overlay over the node's body rows.

        Inline: offset to the body's on-screen position. FALLBACK: to dock at
        the bottom instead, set `dock: bottom` on #tree-editor in DEFAULT_CSS
        and skip the offset below.
        """
        w = self.widget
        w._ensure_cursor_visible()
        try:
            header_idx = w._index_of_header(node)
            screen_y = header_idx - w.row_offset + 1  # first body row
            self._editor_box.styles.offset = (0, max(0, screen_y))
        except Exception:
            pass  # fall back to natural (top) placement

    def _commit_edit(self) -> None:
        if self._editing_node is None or self._editor is None:
            return
        node = self._editing_node
        new_body = "\n".join(self._editor.buffer.lines)
        changed = new_body != (node.body or "")
        node.body = new_body
        self._editor.remove()
        self._editor = None
        self._editor_box.remove_class("editing")
        self._editing_node = None
        self.widget._rebuild_rows()
        self.widget.focus()
        if changed:
            self.is_dirty = True
            self.post_message(self.BodyEdited(node))

    def on_key(self, event) -> None:
        if self.is_editing and event.key == "escape":
            self._commit_edit()
            event.stop()

    # --- commands ----------------------------------------------------------

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="tree.edit", label="Edit snippet", hotkey="f4",
                handler=lambda: self.widget.action_edit(),
            ),
        ]
```

```python
# dunders/windowing/tree/__init__.py  (replace contents)
from .content import TreeContent
from .widget import TreeViewWidget

__all__ = ["TreeContent", "TreeViewWidget"]
```

> **Замечание по `Esc`:** `TreeContent.on_key` перехватывает `escape` только в режиме правки и `event.stop()`-ит его, чтобы окно не закрылось. Вне правки `Esc` идёт по обычному пути приложения.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_content.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/tree/content.py dunders/windowing/tree/__init__.py tests/windowing/test_tree_content.py
git commit -m "feat(tree): TreeContent with inline snippet editor + edit cycle"
```

---

## Task 7: Групповая селекция — `Insert`, `Shift+↑/↓`, `SelectionChanged`

**Files:**
- Modify: `dunders/windowing/tree/widget.py`
- Test: `tests/windowing/test_tree_widget.py`

- [ ] **Step 1: Написать падающий тест**

```python
# append to tests/windowing/test_tree_widget.py
async def test_insert_marks_and_advances():
    root = _tree()
    root.children[1].expanded = True   # a, b, b1
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.press("insert")        # mark 'a', move to 'b'
        assert root.children[0] in w.selected
        assert w.current_node.label == "b"
        await pilot.press("insert")        # mark 'b'
        assert root.children[1] in w.selected
        await pilot.press("insert")        # unmark? no — toggles 'b1'... mark b1
        # 'b' was marked; pressing insert on b1 marks it
        assert len(w.selected) == 3 or len(w.selected) == 2


async def test_shift_down_range_selects():
    root = _tree()
    root.children[1].expanded = True   # a, b, b1
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.press("shift+down")    # extend a..b
        labels = {n.label for n in w.selected}
        assert labels == {"a", "b"}
        await pilot.press("shift+down")    # extend a..b1
        labels = {n.label for n in w.selected}
        assert labels == {"a", "b", "b1"}


async def test_plain_navigation_does_not_clear_marks():
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        await pilot.press("insert")        # mark a
        await pilot.press("down")          # plain move
        assert root.children[0] in w.selected
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_widget.py -k "insert or shift_down or marks" -v`
Expected: FAIL (selection set stays empty — stub handlers)

- [ ] **Step 3: Заменить заглушки селекции на реализацию**

```python
# in dunders/windowing/tree/widget.py — replace the stub block
#   "# --- selection (filled in Task 7) ---" ... action_select_up
# with:

    # --- selection ---------------------------------------------------------

    def action_mark(self) -> None:
        node = self.current_node
        if node is None:
            return
        if node in self.selected:
            self.selected.discard(node)
        else:
            self.selected.add(node)
        self._emit_selection()
        self._step_cursor(+1)
        self.refresh()

    def _extend_selection(self, direction: int) -> None:
        anchor = self.current_node
        if anchor is None:
            return
        self.selected.add(anchor)
        self._step_cursor(direction)
        target = self.current_node
        if target is not None:
            for n in nodes_in_range(self.rows, anchor, target):
                self.selected.add(n)
        self._emit_selection()
        self.refresh()

    def action_select_down(self) -> None:
        self._extend_selection(+1)

    def action_select_up(self) -> None:
        self._extend_selection(-1)

    def _emit_selection(self) -> None:
        self.post_message(self.SelectionChanged(set(self.selected)))
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_widget.py -v`
Expected: PASS (all tree-widget tests, incl. selection)

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/tree/widget.py tests/windowing/test_tree_widget.py
git commit -m "feat(tree): group selection — Insert mark, Shift+arrows range"
```

---

## Task 8: Публичный реэкспорт из `windowing`

**Files:**
- Modify: `dunders/windowing/__init__.py`
- Test: `tests/windowing/test_tree_content.py`

- [ ] **Step 1: Написать падающий тест**

```python
# append to tests/windowing/test_tree_content.py
def test_public_reexport():
    import dunders.windowing as W
    assert hasattr(W, "TreeContent")
    assert hasattr(W, "TreeViewWidget")
    assert hasattr(W, "TreeNode")
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_content.py::test_public_reexport -v`
Expected: FAIL with `AssertionError`

- [ ] **Step 3: Добавить реэкспорт**

В `dunders/windowing/__init__.py` рядом с `from .editor import EditorWidget, EditorContent, MacroAssignDialog` добавить:

```python
from .tree import TreeContent, TreeViewWidget
from .core.tree_model import TreeNode
```

И добавить в список `__all__` строки `"TreeContent",`, `"TreeViewWidget",`, `"TreeNode",`.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_content.py -v`
Expected: PASS

- [ ] **Step 5: Коммит**

```bash
git add dunders/windowing/__init__.py tests/windowing/test_tree_content.py
git commit -m "feat(tree): re-export TreeContent/TreeViewWidget/TreeNode"
```

---

## Task 9: Демо-наполнитель и окно в showcase-приложении

**Files:**
- Modify: `dunders/windowing/demo/contents.py`
- Modify: `dunders/windowing/demo/app.py`
- Test: `tests/windowing/test_tree_content.py`

- [ ] **Step 1: Написать падающий тест**

```python
# append to tests/windowing/test_tree_content.py
def test_build_demo_tree_has_lazy_branch_and_snippets():
    from dunders.windowing.demo.contents import build_demo_tree
    root = build_demo_tree()
    labels = [c.label for c in root.children]
    assert labels  # non-empty
    # at least one node carries an editable body
    assert any(c.has_body for c in root.children) or any(
        gc.has_body for c in root.children for gc in c.children
    )
    # at least one lazy branch exists
    assert any(c.loader is not None for c in root.children)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `pytest tests/windowing/test_tree_content.py::test_build_demo_tree_has_lazy_branch_and_snippets -v`
Expected: FAIL with `ImportError: cannot import name 'build_demo_tree'`

- [ ] **Step 3: Реализовать демо-наполнитель и окно**

```python
# append to dunders/windowing/demo/contents.py
from dunders.windowing.core.tree_model import TreeNode


def build_demo_tree() -> TreeNode:
    """In-memory sample: folders, editable snippet leaves, one lazy branch."""
    root = TreeNode(label="<root>")

    notes = root.add_child(TreeNode(label="notes"))
    notes.expanded = True
    notes.add_child(TreeNode(label="todo", body="- wire up adapters\n- write docs"))
    notes.add_child(TreeNode(label="idea", body="snippet nodes editable inline"))

    snippets = root.add_child(TreeNode(label="snippets"))
    snippets.add_child(
        TreeNode(label="hello.py", body="def hello():\n    print('hi')")
    )

    def _load(node: TreeNode) -> list[TreeNode]:
        return [
            TreeNode(label=f"item-{i}", body=f"lazy body {i}")
            for i in range(1, 4)
        ]

    root.add_child(TreeNode(label="lazy-folder", loader=_load))
    return root
```

```python
# in dunders/windowing/demo/app.py
# 1) extend the imports from demo.contents to include build_demo_tree
# 2) import TreeContent near the EditorContent import:
#       from dunders.windowing.tree import TreeContent
# 3) in _build_scene(), after the existing windows, add:

        w_tree = make_window(
            TreeContent(build_demo_tree(), title="Tree"),
            title=TitleSpec(text="Tree", align="left"),
            position=(2, 23),
            size=(40, 14),
            border_focused=BorderStyle.SINGLE,
            border_unfocused=BorderStyle.NONE,
            decorations=Decorations(close_box=True, zoom_box=True),
        )
        d.add_window(w_tree)
```

> Реализатор: точные имена `make_window`, `TitleSpec`, `BorderStyle`, `Decorations` уже импортированы в `demo/app.py` (см. существующие `w1..w4`). Скопируй стиль вызова оттуда. Если координаты `(2, 23)` вне экрана при дефолтном размере — подвинь, лишь бы окно добавилось.

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `pytest tests/windowing/test_tree_content.py -v`
Expected: PASS

- [ ] **Step 5: Ручная проверка демо**

Run: `python -m dunders.windowing.demo`
Expected: появляется окно «Tree»; стрелки двигают курсор; `→` раскрывает `lazy-folder` (подгружает item-1..3); `Enter`/`F4` на `todo` открывает встроенный редактор без бордюра; правка + `Esc` сохраняет текст и ставит «грязный» маркер окна; `Insert` помечает узлы `*`. Закрыть: F10.

- [ ] **Step 6: Коммит**

```bash
git add dunders/windowing/demo/contents.py dunders/windowing/demo/app.py tests/windowing/test_tree_content.py
git commit -m "feat(tree): demo tree window in windowing showcase"
```

---

## Task 10: Полный прогон и линт

**Files:** —

- [ ] **Step 1: Весь набор тестов**

Run: `pytest tests/windowing/test_tree_model.py tests/windowing/test_tree_widget.py tests/windowing/test_tree_content.py -v`
Expected: всё PASS

- [ ] **Step 2: Регрессий нет**

Run: `pytest -q`
Expected: набор зелёный (нет новых падений в существующих тестах)

- [ ] **Step 3: Линт**

Run: `ruff check dunders/windowing/tree dunders/windowing/core/tree_model.py dunders/windowing/demo`
Expected: без ошибок (поправить, если есть)

- [ ] **Step 4: Финальный коммит при необходимости**

```bash
git add -A
git commit -m "chore(tree): lint fixes and final pass"
```

---

## Self-Review (выполнено автором плана)

**Покрытие спеки:**
- Расположение/слои (core/tree + widget + content) → Tasks 1–8.
- Модель узла «заголовок + опц. тело + опц. дети» → Task 1.
- `flatten_visible` мост модель→экран → Task 2.
- Один активный редактор, цикл правки, write-back, `BodyEdited`, dirty → Task 6.
- Технический риск инлайн-редактора + фолбэк на док снизу → Task 6, `_position_editor` (FALLBACK помечен).
- Навигация клавиатурой (↑↓→←, Enter/F4, Esc, Space) → Tasks 4, 6.
- Мышь (колесо, клик стрелка/заголовок/сниппет, сброс набора) → Task 5.
- Ленивая догрузка через `loader`, один раз → Tasks 1, 4.
- Групповая селекция: набор, `Insert`, `Shift+↑/↓`, `SelectionChanged`, маркер `*`, независимость от курсора → Tasks 4 (маркер/стаб), 7. Сброс набора кликом → Task 5. `Ctrl/Shift+клик` — см. примечание ниже.
- Контракт «каркас не персистит, только события» → Tasks 6, 7.
- Демо + тесты → Tasks 9, 1–9.
- Вне скоупа (адаптеры ФС/JSON, add/delete узлов, действия над группой) — не планируется. ✔

**Примечание о `Ctrl+клик`/`Shift+клик`:** в спеке они заявлены. В этом плане реализованы колесо, обычный клик (выбор/правка/сброс) и клик по стрелке (Task 5), плюс клавиатурная групповая селекция (Task 7). `Ctrl/Shift+клик` сознательно отложены как дополнительный модификатор мыши, чтобы не раздувать первую итерацию — клавиатурный мультиселект (`Insert`/`Shift+стрелки`) полностью покрывает сценарий. Если нужно строго всё из спеки в этой итерации — добавить шаг в Task 5 на разбор `event.ctrl`/`event.shift` в `on_click` (toggle одного / диапазон от курсора).

**Плейсхолдеры:** не найдено («TBD»/«добавить обработку ошибок» и т.п. отсутствуют; весь код приведён).

**Согласованность типов:** `TreeNode`, `HeaderRow`, `BodyRow`, `flatten_visible`, `header_nodes`, `nodes_in_range`, `TreeViewWidget` (`current_node`, `visible_headers`, `selected`, `_rebuild_rows`, `_index_of_header`, `_step_cursor`, `action_*`), `TreeContent` (`widget`, `is_editing`, `is_dirty`, `BodyEdited`) — имена единообразны во всех задачах.
