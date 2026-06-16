"""TreeViewWidget — line-painted tree with one inline-editable snippet at a time.

Follows the FilePanel idiom: a plain Widget that paints via ``render_line`` and
keeps its own integer ``cursor`` (always on a HeaderRow) and ``row_offset``.
"""

from __future__ import annotations

from typing import Callable

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from rich.segment import Segment
from rich.style import Style as RichStyle

from dunders.windowing.core.tree_model import (
    BodyRow, HeaderRow, Row, TreeNode,
    flatten_visible, header_nodes, label_lines, nodes_in_range,
    visible_label_lines,
)

_INDENT = 2
_WHEEL_ROWS = 3


def body_indent(depth: int) -> int:
    """Left indent (columns) for a node's body, aligned under its label.

    Header layout: mark(1) + ancestor columns(2*(depth-1)) + connector(2) +
    dot(1) + toggle(1) + space(1). For depth 0 the connector is empty, so the
    formula collapses to ``depth*_INDENT + 4`` for every depth.
    """
    return depth * _INDENT + 4


def _is_last_child(node: "TreeNode") -> bool:
    p = node.parent
    return p is None or not p.children or p.children[-1] is node


def tint_bg(hexcol: str | None) -> str | None:
    """Nudge a ``#rrggbb`` colour lighter (on dark themes) or darker (on light)
    so the snippet body band stands out from the surrounding tree content."""
    if not hexcol or not hexcol.startswith("#") or len(hexcol) != 7:
        return hexcol
    r, g, b = int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)
    amt = 0x16 if (r + g + b) / 3 < 128 else -0x16
    clamp = lambda v: max(0, min(255, v + amt))  # noqa: E731
    return f"#{clamp(r):02x}{clamp(g):02x}{clamp(b):02x}"


def brighten(hexcol: str | None) -> str | None:
    """Push a colour toward white — used for the bright in-text caret block."""
    if not hexcol or not hexcol.startswith("#") or len(hexcol) != 7:
        return hexcol
    r, g, b = int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)
    up = lambda v: round(v + (255 - v) * 0.55)  # noqa: E731
    return f"#{up(r):02x}{up(g):02x}{up(b):02x}"


def dim_fg(hexcol: str | None) -> str | None:
    """Muted text colour for body lines, distinct from the (brighter) label
    text — blended toward a neutral grey with a slight cool tint."""
    if not hexcol or not hexcol.startswith("#") or len(hexcol) != 7:
        return hexcol
    r, g, b = int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)
    mix = lambda v, t: round(v * 0.6 + t * 0.4)  # noqa: E731
    return f"#{mix(r, 0x6f):02x}{mix(g, 0x80):02x}{mix(b, 0x90):02x}"


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
        Binding("delete", "delete", show=False),
        Binding("ctrl+n", "new_node", show=False),
        Binding("ctrl+t", "new_child", show=False),       # reliable
        Binding("ctrl+right", "new_child", show=False),   # nicer where it arrives
        Binding("ctrl+e", "edit_body", show=False),
    ]

    class EditRequested(Message):
        """Posted when the user asks to edit a node's text field.

        ``kind`` is ``"label"`` or ``"body"``; ``line``/``col`` place the caret
        where the user clicked / navigated (so it doesn't jump to the start).
        """
        def __init__(
            self, node: TreeNode, kind: str = "body", line: int = 0, col: int = 0
        ) -> None:
            super().__init__()
            self.node = node
            self.kind = kind
            self.line = line
            self.col = col

    class SelectionChanged(Message):
        def __init__(self, nodes: set[TreeNode]) -> None:
            super().__init__()
            self.nodes = nodes

    class Deleted(Message):
        """Posted after Del removes a node (``kind="node"``) or clears a node's
        body (``kind="body"``)."""
        def __init__(self, node: TreeNode, kind: str) -> None:
            super().__init__()
            self.node = node
            self.kind = kind

    class Inserted(Message):
        """Posted after a new node (``kind="node"``) or a new empty body
        (``kind="body"``) is created."""
        def __init__(self, node: TreeNode, kind: str) -> None:
            super().__init__()
            self.node = node
            self.kind = kind

    def __init__(self, root: TreeNode, label_display: str = "all", **kwargs) -> None:
        super().__init__(**kwargs)
        self.root = root
        # How multi-line labels render: "all" | "first" | "inline".
        self.label_display = label_display
        self.rows: list[Row] = []
        self.cursor: int = 0
        self.row_offset: int = 0
        self.selected: set[TreeNode] = set()
        # Shift+arrow range selection: anchor + the marks that existed when the
        # range started, so moving back shrinks the range (instead of only
        # growing). Cleared by plain navigation / Insert.
        self._sel_anchor: TreeNode | None = None
        self._sel_base: set[TreeNode] = set()
        self._sel_mode: str = "select"   # or "deselect" — set from the anchor
        self._sb_drag = False            # dragging the scrollbar thumb
        # While a field is being edited, its rows are reserved (kept in the flow
        # so following rows shift down) but painted blank apart from the guide
        # columns — the inline EditorWidget overlay draws the text.
        self.editing_node: TreeNode | None = None
        self.editing_kind: str = "body"   # "label" | "body" — which field
        # Set by TreeContent: commit the in-progress edit synchronously. Called
        # at the start of on_click so the overlay is gone before any reflow.
        self.request_commit: "Callable[[], None] | None" = None
        # Set by TreeContent: re-place the inline editor after the tree scrolls.
        self.request_reposition: "Callable[[], None] | None" = None
        self._rebuild_rows()

    # --- model -> rows -----------------------------------------------------

    def _rebuild_rows(self) -> None:
        expand = self.editing_node if self.editing_kind == "label" else None
        self.rows = flatten_visible(self.root, self.label_display, expand_label=expand)
        self.cursor = max(0, min(self.cursor, len(self.rows) - 1)) if self.rows else 0
        self.refresh()

    @property
    def current_node(self) -> TreeNode | None:
        if 0 <= self.cursor < len(self.rows):
            return self.rows[self.cursor].node
        return None

    @property
    def current_field(self) -> tuple[TreeNode, str] | None:
        """``(node, "label"|"body")`` for the row under the cursor."""
        if not (0 <= self.cursor < len(self.rows)):
            return None
        row = self.rows[self.cursor]
        return (row.node, "body" if isinstance(row, BodyRow) else "label")

    def visible_headers(self) -> list[TreeNode]:
        return header_nodes(self.rows)

    # --- navigation (per text line) ----------------------------------------

    def _move_cursor(self, direction: int) -> None:
        if not self.rows:
            return
        self._sel_anchor = None   # plain navigation ends a shift-selection
        new = max(0, min(self.cursor + direction, len(self.rows) - 1))
        if new != self.cursor:
            self.cursor = new
            self._ensure_cursor_visible()
            self.refresh()

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def action_cursor_down(self) -> None:
        self._move_cursor(+1)

    def _step_to_node(self, direction: int) -> None:
        """Move the cursor to the primary line of the previous/next node
        (selection is node-based, so it jumps node to node)."""
        cur = self.current_node
        i = self.cursor + direction
        while 0 <= i < len(self.rows):
            r = self.rows[i]
            if isinstance(r, HeaderRow) and r.line_index == 0 and r.node is not cur:
                self.cursor = i
                self._ensure_cursor_visible()
                self.refresh()
                return
            i += direction

    # ← / → reveal/hide a node's content one step at a time: body first, then
    # sub-branches; collapse in reverse.
    def action_expand(self) -> None:
        node = self.current_node
        if node is None:
            return
        if node.has_body and not node.body_open:
            node.body_open = True
            self._rebuild_rows()
        elif node.is_branch and not node.expanded:
            node.ensure_loaded()
            node.expanded = True
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
        """Space / arrow-click: toggle children only."""
        node = self.current_node
        if node is None or not node.is_branch:
            return
        if node.expanded:
            node.expanded = False
        else:
            node.ensure_loaded()
            node.expanded = True
        self._rebuild_rows()

    def action_expand_all(self) -> None:
        """Recursively reveal everything: open every body and expand every
        branch (loading lazy children)."""
        def walk(node: TreeNode) -> None:
            if node.has_body:
                node.body_open = True
            if node.is_branch:
                node.ensure_loaded()
                node.expanded = True
            for child in node.children:
                walk(child)
        for child in self.root.children:
            walk(child)
        self._rebuild_rows()

    def action_collapse_all(self) -> None:
        """Recursively hide everything: collapse branches and close bodies."""
        def walk(node: TreeNode) -> None:
            node.expanded = False
            node.body_open = False
            for child in node.children:
                walk(child)
        for child in self.root.children:
            walk(child)
        self._rebuild_rows()

    def action_edit(self) -> None:
        """Enter: edit the field under the cursor, caret on the current line."""
        if not (0 <= self.cursor < len(self.rows)):
            return
        row = self.rows[self.cursor]
        kind = "body" if isinstance(row, BodyRow) else "label"
        self.post_message(self.EditRequested(row.node, kind, line=row.line_index))

    def _select_node(self, node: TreeNode) -> None:
        for i, r in enumerate(self.rows):
            if isinstance(r, HeaderRow) and r.line_index == 0 and r.node is node:
                self.cursor = i
                self._ensure_cursor_visible()
                self.refresh()
                return

    def action_delete(self) -> None:
        """Del: clear the body if the cursor is on a body line, otherwise
        delete the whole node."""
        if not (0 <= self.cursor < len(self.rows)):
            return
        row = self.rows[self.cursor]
        node = row.node
        if isinstance(row, BodyRow):
            node.body = None
            node.body_open = False
            self._rebuild_rows()
            self._select_node(node)
            self.post_message(self.Deleted(node, "body"))
        else:
            self._delete_node(node)

    def _delete_node(self, node: TreeNode) -> None:
        parent = node.parent
        if parent is None:
            return
        siblings = parent.children
        pos = next((i for i, c in enumerate(siblings) if c is node), -1)
        if pos < 0:
            return
        self.selected.discard(node)
        del siblings[pos]
        self._rebuild_rows()
        # Land the cursor on the next sibling, else the previous, else parent.
        if siblings:
            target = siblings[min(pos, len(siblings) - 1)]
            self._select_node(target)
        elif parent is not self.root:
            self._select_node(parent)
        else:
            self.cursor = max(0, min(self.cursor, len(self.rows) - 1))
            self._ensure_cursor_visible()
            self.refresh()
        self.post_message(self.Deleted(node, "node"))

    def action_new_node(self) -> None:
        """Ctrl+N: insert a new sibling below the current node and edit it."""
        node = self.current_node
        if node is None:
            parent, pos = self.root, len(self.root.children)
        else:
            parent = node.parent or self.root
            here = next((i for i, c in enumerate(parent.children) if c is node), -1)
            pos = here + 1 if here >= 0 else len(parent.children)
        new = TreeNode(label="")
        new.parent = parent
        parent.children.insert(pos, new)
        self._rebuild_rows()
        self._select_node(new)
        self.post_message(self.Inserted(new, "node"))
        self.post_message(self.EditRequested(new, "label"))

    def action_new_child(self) -> None:
        """Ctrl+Right: insert a new child of the current node and edit it."""
        node = self.current_node
        if node is None:
            return
        node.ensure_loaded()
        new = TreeNode(label="")
        new.parent = node
        node.children.insert(0, new)   # first child: appears right under parent
        node.expanded = True
        self._rebuild_rows()
        self._select_node(new)
        self.post_message(self.Inserted(new, "node"))
        self.post_message(self.EditRequested(new, "label"))

    def action_edit_body(self) -> None:
        """Ctrl+E: edit the current node's body, creating an empty one if it
        has none."""
        node = self.current_node
        if node is None:
            return
        created = node.body is None
        if created:
            node.body = ""
            self.post_message(self.Inserted(node, "body"))
        self.post_message(self.EditRequested(node, "body", line=0))

    # --- selection ---------------------------------------------------------

    def action_mark(self) -> None:
        node = self.current_node
        if node is None:
            return
        self._sel_anchor = None   # Insert toggles a single mark, not a range
        if node in self.selected:
            self.selected.discard(node)
        else:
            self.selected.add(node)
        self._emit_selection()
        self._step_to_node(+1)
        self.refresh()

    def _extend_selection(self, direction: int) -> None:
        node = self.current_node
        if node is None:
            return
        if self._sel_anchor is None:           # start a new range here
            self._sel_anchor = node
            self._sel_base = set(self.selected)
            # mc-style: the anchor's state decides whether the drag selects or
            # deselects — Shift over already-marked nodes clears them.
            self._sel_mode = "deselect" if node in self.selected else "select"
        self._step_to_node(direction)
        target = self.current_node
        # The contiguous span anchor..cursor; moving back shrinks it.
        rng = set(nodes_in_range(self.rows, self._sel_anchor, target)) if target \
            else {self._sel_anchor}
        if self._sel_mode == "deselect":
            self.selected = self._sel_base - rng
        else:
            self.selected = self._sel_base | rng
        self._emit_selection()
        self.refresh()

    def action_select_down(self) -> None:
        self._extend_selection(+1)

    def action_select_up(self) -> None:
        self._extend_selection(-1)

    def clear_selection(self) -> None:
        if not self.selected:
            return
        self.selected.clear()
        self._sel_anchor = None
        self._emit_selection()
        self.refresh()

    def on_key(self, event: events.Key) -> None:
        # Esc (in navigation) clears the whole selection; only consume it when
        # there is something to clear, so an empty-selection Esc still bubbles.
        if event.key == "escape" and self.selected:
            self.clear_selection()
            event.stop()
            event.prevent_default()

    def _emit_selection(self) -> None:
        self.post_message(self.SelectionChanged(set(self.selected)))

    # --- mouse -------------------------------------------------------------

    def _row_at(self, y: int) -> int:
        idx = y + self.row_offset
        if 0 <= idx < len(self.rows):
            return idx
        return -1

    def _on_scrollbar(self, event) -> bool:
        active, _start, _size = self._scrollbar_geom()
        return active and event.x >= self.size.width - 1

    def _sb_scroll_to(self, y: int) -> None:
        h = self.size.height
        max_off = max(0, len(self.rows) - h)
        frac = y / max(1, h - 1)
        self.row_offset = max(0, min(max_off, round(frac * max_off)))
        self._after_scroll()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._on_scrollbar(event):
            self._sb_drag = True
            self.capture_mouse()
            self._sb_scroll_to(event.y)
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._sb_drag:
            self._sb_scroll_to(event.y)
            event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._sb_drag:
            self._sb_drag = False
            self.release_mouse()
            event.stop()

    def on_click(self, event: events.Click) -> None:
        if self._on_scrollbar(event):
            event.stop()
            return
        idx = self._row_at(event.y)
        if idx < 0:
            return
        row = self.rows[idx]
        node = row.node

        # Commit any in-progress edit FIRST (synchronously) so its overlay is
        # gone and the tree has reflowed before we act on this click.
        if self.editing_node is not None:
            editing = self.editing_node
            if self.request_commit is not None:
                self.request_commit()
            if node is editing:
                # Second click on the node we were editing -> leave it closed.
                self._select_node(node)
                self.refresh()
                return

        # Click on the expand arrow of a branch's primary line toggles children.
        if isinstance(row, HeaderRow) and row.line_index == 0 and node.is_branch:
            toggle_x = len(self._primary_gutter(node)) - 2
            if event.x == toggle_x:
                self._select_node(node)
                self.action_toggle_expand()
                self.refresh()
                return

        # Otherwise: open and edit the body (click always targets the body),
        # placing the caret where the click landed.
        self.cursor = idx
        self._ensure_cursor_visible()
        line = row.line_index if isinstance(row, BodyRow) else 0
        col = max(0, event.x - body_indent(row.depth))
        self.post_message(self.EditRequested(node, "body", line=line, col=col))
        self.refresh()

    def _index_of_header(self, node: TreeNode) -> int:
        for i, r in enumerate(self.rows):
            if isinstance(r, HeaderRow) and r.node is node:
                return i
        return self.cursor

    def field_row_index(self, node: TreeNode, kind: str) -> int:
        """Row index of the first line of ``node``'s ``label``/``body`` field."""
        for i, r in enumerate(self.rows):
            if r.node is not node:
                continue
            if kind == "label" and isinstance(r, HeaderRow) and r.line_index == 0:
                return i
            if kind == "body" and isinstance(r, BodyRow) and r.line_index == 0:
                return i
        return self.cursor

    def _after_scroll(self) -> None:
        self.refresh()
        if self.editing_node is not None and self.request_reposition is not None:
            self.request_reposition()   # keep the inline editor on its node

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.row_offset = min(
            max(0, len(self.rows) - 1), self.row_offset + _WHEEL_ROWS
        )
        self._after_scroll()
        event.stop()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.row_offset = max(0, self.row_offset - _WHEEL_ROWS)
        self._after_scroll()
        event.stop()

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

    def _row_is_editing(self, row: Row) -> bool:
        if row.node is not self.editing_node:
            return False
        kind = "body" if isinstance(row, BodyRow) else "label"
        return kind == self.editing_kind

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if width <= 0:
            return Strip.blank(0)
        active, thumb_start, thumb_size = self._scrollbar_geom()
        cw = width - 1 if active else width   # reserve a column for the scrollbar
        idx = y + self.row_offset
        if idx >= len(self.rows):
            content = Strip([Segment(" " * cw)])
        else:
            content = self._render_row(self.rows[idx], idx, cw)
        if not active:
            return content
        on_thumb = thumb_start <= y < thumb_start + thumb_size
        glyph, style = (("▌", self._sb_thumb_style()) if on_thumb
                        else ("▏", RichStyle(dim=True)))
        return Strip([*content, Segment(glyph, style)])

    def _render_row(self, row: Row, idx: int, width: int) -> Strip:
        # The list-row cursor is a soft highlight applied LAST so its background
        # wins over the field colours (and reads differently from the bright
        # in-text caret).
        cur = self._cursor_style() if idx == self.cursor else RichStyle()
        gutter = self._row_gutter(row)
        if isinstance(row, BodyRow):
            return self._render_body_row(row, cur, gutter, width)
        # Label / header row (the editor overlay covers the text when editing).
        if self._row_is_editing(row):
            return Strip([Segment(gutter[:width].ljust(width), self._field_style(row) + cur)])
        text = (gutter + self._label_line(row))[:width].ljust(width)
        return Strip([Segment(text, self._field_style(row) + cur)])

    def _scrollbar_geom(self) -> tuple[bool, int, int]:
        """(active, thumb_start, thumb_size) for the vertical scrollbar."""
        h = self.size.height
        total = len(self.rows)
        if h <= 0 or total <= h:
            return (False, 0, 0)
        thumb = max(1, round(h * h / total))
        max_off = max(1, total - h)
        start = round(self.row_offset / max_off * (h - thumb))
        return (True, max(0, min(start, h - thumb)), thumb)

    def _sb_thumb_style(self) -> RichStyle:
        fg = self._content_fg()
        return RichStyle(color=fg) if fg else RichStyle(reverse=True)

    def _render_body_row(self, row: BodyRow, cur: RichStyle, gutter: str, width: int) -> Strip:
        bi = body_indent(row.depth)
        guides = gutter[: max(0, bi - 1)]
        body_style = self._field_style(row) + cur           # muted fg + tint bg
        fg = self._content_fg()
        border_style = (RichStyle(color=fg) if fg else RichStyle()) + cur
        segs = [Segment(guides, cur), Segment("▏", border_style)]  # thin left border
        rest = max(0, width - bi)
        if self._row_is_editing(row):
            segs.append(Segment(" " * rest, body_style))    # editor covers the text
        else:
            segs.append(Segment(self._body_line(row)[:rest].ljust(rest), body_style))
        return Strip(segs)

    def _cursor_style(self) -> RichStyle:
        """Soft highlight for the navigation row cursor (distinct from, and less
        bright than, the in-text caret)."""
        pal = self._palette()
        if pal is None:
            return RichStyle(reverse=True)
        bg = tint_bg(tint_bg(pal.get("window.content").bg))   # a couple steps up
        return RichStyle(bgcolor=bg) if bg else RichStyle(reverse=True)

    def _content_fg(self) -> str | None:
        pal = self._palette()
        return pal.get("window.content").fg if pal else None

    def _field_style(self, row: Row) -> RichStyle:
        """Per-field colours: bright label text vs muted body text on a faintly
        tinted body background."""
        pal = self._palette()
        if pal is None:
            return RichStyle()
        content = pal.get("window.content")
        fg = content.fg
        if isinstance(row, BodyRow):
            st = RichStyle()
            bf = dim_fg(fg)
            if bf:
                st += RichStyle(color=bf)
            bg = tint_bg(content.bg)
            if bg:
                st += RichStyle(bgcolor=bg)
            return st
        return RichStyle(color=fg) if fg else RichStyle()

    def _palette(self):
        for a in self.ancestors_with_self:
            pal = getattr(a, "palette", None)
            if pal is not None and hasattr(pal, "get"):
                return pal
        return None

    def _body_bg(self) -> str | None:
        """Distinct background for snippet body rows so the body is obvious."""
        pal = self._palette()
        if pal is None:
            return None
        return tint_bg(pal.get("window.content").bg)


    def _guide_prefix(self, node: TreeNode) -> str:
        """Vertical guide columns for ancestors STRICTLY above the node's
        parent: ``│ `` where that ancestor has a following sibling (its branch
        line continues), ``  `` otherwise. The parent itself is drawn by the
        connector, which lands directly under the parent's dot."""
        cols: list[str] = []
        n = node.parent.parent if node.parent is not None else None
        while n is not None and n.parent is not None:  # exclude the hidden root
            cols.append("  " if _is_last_child(n) else "│ ")
            n = n.parent
        cols.reverse()
        return "".join(cols)

    def _primary_gutter(self, node: TreeNode) -> str:
        """The left part of a node's primary line: mark + guides + connector +
        dot + toggle + space (width == body_indent)."""
        mark = "*" if node in self.selected else " "
        prefix = self._guide_prefix(node)
        # Top-level nodes (parent is the hidden root) carry no connector — their
        # dot sits at the left so descendants' branch lines drop from under it.
        if node.parent is not None and node.parent.parent is not None:
            connector = "└─" if _is_last_child(node) else "├─"
        else:
            connector = ""
        toggle = ("▾" if node.expanded else "▸") if node.is_branch else " "
        dot = "●" if node.has_body else "○"  # filled = has editable body
        return f"{mark}{prefix}{connector}{dot}{toggle} "

    def _row_gutter(self, row: Row) -> str:
        """Left columns (width body_indent) before the editable text: the dot
        line for a node's primary label line, guides for every other row."""
        if isinstance(row, HeaderRow) and row.line_index == 0:
            return self._primary_gutter(row.node)
        return self._body_prefix(row.node, row.depth)

    def _label_line(self, row: HeaderRow) -> str:
        node = row.node
        if node is self.editing_node and self.editing_kind == "label":
            lines = label_lines(node)               # editing shows every line
        else:
            lines = visible_label_lines(node, self.label_display)
        return lines[row.line_index] if row.line_index < len(lines) else ""

    def _header_text(self, row: HeaderRow) -> str:
        return self._row_gutter(row) + self._label_line(row)

    def _body_prefix(self, node: TreeNode, depth: int) -> str:
        """Guide columns drawn through a node's body rows so branch lines that
        pass by stay continuous: ancestor verticals, the node's own vertical
        down to its next sibling, and down to its children (which follow the
        body in the flow)."""
        cols = [" "] * body_indent(depth)
        gp = self._guide_prefix(node)            # ancestor verticals, from col 1
        for i, ch in enumerate(gp):
            cols[1 + i] = ch
        if depth >= 1 and not _is_last_child(node):
            cols[2 * depth - 1] = "│"            # connector column -> next sibling
        if node.expanded and node.children:
            cols[2 * depth + 1] = "│"            # dot column -> children below body
        return "".join(cols)

    def _body_line(self, row: BodyRow) -> str:
        lines = (row.node.body or "").split("\n")
        return lines[row.line_index] if row.line_index < len(lines) else ""

    def _body_text(self, row: BodyRow) -> str:
        return self._body_prefix(row.node, row.depth) + self._body_line(row)

    def header_depth(self, node: TreeNode) -> int:
        for r in self.rows:
            if isinstance(r, HeaderRow) and r.node is node:
                return r.depth
        return 0
