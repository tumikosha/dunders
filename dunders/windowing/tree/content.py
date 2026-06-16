"""TreeContent — WindowContent dunder hosting a TreeViewWidget plus one
inline EditorWidget mounted on demand to edit a node's snippet body."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.strip import Strip
from textual.widget import Widget

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style as RichStyle

from dunders.windowing.content import WindowContent, WindowCommand
from dunders.windowing.core.buffer import TextBuffer
from dunders.windowing.core.tree_model import BodyRow, TreeNode
from dunders.windowing.editor.widget import EditorWidget
from dunders.windowing.palette import Style
from dunders.windowing.tree.widget import (
    TreeViewWidget, body_indent, brighten, dim_fg, tint_bg,
)


class TreeFieldEditor(EditorWidget):
    """Inline editor that, instead of clamping the caret at the first/last
    line, posts ``CursorEscaped`` so the host can leave edit mode and move the
    tree cursor to the adjacent row. Also forwards tree commands (new node /
    child / body) to the host so they work while editing.

    Note: Ctrl+Right is repurposed for "new child" — word-right stays on
    Alt+Right (the editor binds both)."""

    # Tree commands handled directly in on_key (NOT via Binding) so the event is
    # stopped before it bubbles to the app — otherwise the windowing key router
    # would also fire the same hotkey on another window (e.g. Ctrl+] folding a
    # neighbouring editor). Ctrl+] (0x1D) is distinct from Esc (Ctrl+[ == Esc).
    _TREE_KEYS = {
        "ctrl+n": "new_node",
        "ctrl+t": "new_child",          # reliable
        "ctrl+right": "new_child",      # nicer where the terminal delivers it
        "ctrl+e": "edit_body",
        "ctrl+right_square_bracket": "toggle_fold",
    }

    class CursorEscaped(Message):
        def __init__(self, direction: int) -> None:
            super().__init__()
            self.direction = direction

    class TreeCommand(Message):
        def __init__(self, name: str) -> None:
            super().__init__()
            self.name = name

    def on_key(self, event: events.Key) -> None:
        name = self._TREE_KEYS.get(event.key)
        if name is not None:
            event.stop()
            event.prevent_default()
            self.post_message(self.TreeCommand(name))
            return
        super().on_key(event)

    def action_cursor_up(self) -> None:
        if self.buffer.cursor_row == 0:
            self.post_message(self.CursorEscaped(-1))
            return
        super().action_cursor_up()

    def action_cursor_down(self) -> None:
        if self.buffer.cursor_row >= self.buffer.line_count - 1:
            self.post_message(self.CursorEscaped(+1))
            return
        super().action_cursor_down()


class TreeActionBar(Widget):
    """A one-row strip of single-glyph buttons that run tree operations on the
    current node. Buttons highlight on hover and show a name+hotkey tooltip;
    clicks map to a button by its rendered cell range."""

    DEFAULT_CSS = """
    TreeActionBar { dock: bottom; height: 1; layer: base; background: $panel; }
    """

    class Pressed(Message):
        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    def __init__(self, buttons: list[tuple[str, str, str]], **kwargs) -> None:
        super().__init__(**kwargs)
        # (glyph, action, tooltip)
        self.buttons = buttons
        self._hover = -1   # index of the button under the pointer, -1 = none

    def _cells(self) -> list[tuple[int, int, str]]:
        """(cell_start, cell_end, action) per button — cell includes padding."""
        cells, x = [], 0
        for glyph, action, _tip in self.buttons:
            w = cell_len(f" {glyph} ")
            cells.append((x, x + w, action))
            x += w
        return cells

    def _button_at(self, px: int) -> int:
        for i, (start, end, _a) in enumerate(self._cells()):
            if start <= px < end:
                return i
        return -1

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        segs: list[Segment] = []
        used = 0
        for i, (glyph, _action, _tip) in enumerate(self.buttons):
            cell = f" {glyph} "
            if i == self._hover:
                style = RichStyle(reverse=True, bold=True)   # highlighted
            else:
                style = RichStyle(dim=True)
            segs.append(Segment(cell, style))
            used += cell_len(cell)
        if used < width:
            segs.append(Segment(" " * (width - used)))
        return Strip(segs)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        idx = self._button_at(event.x)
        if idx != self._hover:
            self._hover = idx
            self.tooltip = self.buttons[idx][2] if idx >= 0 else None
            self.refresh()

    def on_leave(self, event: events.Leave) -> None:
        if self._hover != -1:
            self._hover = -1
            self.tooltip = None
            self.refresh()

    def on_click(self, event: events.Click) -> None:
        idx = self._button_at(event.x)
        if idx >= 0:
            self.post_message(self.Pressed(self.buttons[idx][1]))
        event.stop()


class TreeContent(WindowContent):
    """Dunder: editable snippet tree."""

    DEFAULT_CSS = """
    TreeContent { background: transparent; layers: base overlay; }
    TreeContent TreeViewWidget { width: 1fr; height: 1fr; layer: base; }
    TreeContent #tree-editor {
        layer: overlay;
        background: $surface;
        display: none;
        height: 1;
    }
    TreeContent #tree-editor.editing { display: block; }
    TreeContent #tree-editor EditorWidget {
        border: none;
        background: $surface;
        height: 1fr;
        /* Horizontal bar shows for long lines; _position_editor reserves an
           extra row for it so it never sits on top of the last body line.
           Size + colour come from EditorWidget's own DEFAULT_CSS so the inline
           and F4 editors share one look. */
        overflow-x: auto;
    }
    """

    # (glyph, action, tooltip) for the bottom action bar.
    ACTION_BUTTONS = [
        ("⊕", "new_node", "New sibling (Ctrl+N)"),
        ("↳", "new_child", "New child (Ctrl+T)"),
        ("✎", "edit_body", "Edit body (Ctrl+E)"),
        ("⊟", "toggle_fold", "Fold / unfold"),
        ("⊞", "expand_all", "Unfold all"),
        ("✕", "delete", "Delete (Del)"),
    ]

    class BodyEdited(Message):
        def __init__(self, node: TreeNode) -> None:
            super().__init__()
            self.node = node

    def __init__(
        self, root: TreeNode, title: str | None = None,
        label_display: str = "all", **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.root = root
        self.widget = TreeViewWidget(root, label_display=label_display)
        self._action_bar = TreeActionBar(self.ACTION_BUTTONS)
        # Let the widget commit an in-progress edit synchronously on click,
        # before it reflows the tree (prevents the overlay from lingering).
        self.widget.request_commit = self._commit_edit
        # Re-place the inline editor when the tree scrolls (wheel) so the
        # overlay tracks the node instead of staying put.
        self.widget.request_reposition = self._position_editor
        self._editor_box = Container(id="tree-editor")
        self._editor: EditorWidget | None = None
        self._editing_node: TreeNode | None = None
        self._editing_kind: str = "body"   # "label" | "body"
        self._orig_text: str = ""
        # Freshly inserted nodes: removed on commit if left empty (cancelled).
        self._fresh_nodes: set[TreeNode] = set()
        if title is not None:
            self.window_title = title

    @property
    def is_editing(self) -> bool:
        return self._editing_node is not None

    def compose(self) -> ComposeResult:
        yield self.widget
        yield self._editor_box
        yield self._action_bar

    def on_tree_action_bar_pressed(self, event: TreeActionBar.Pressed) -> None:
        # Commit any edit first, then run the action on the current node and
        # return focus to the tree.
        if self.is_editing:
            self._commit_edit()
        self._run_action(event.action)
        if self.widget.is_mounted:
            self.widget.focus()

    def _run_action(self, action: str) -> None:
        fn = {
            "new_node": self.widget.action_new_node,
            "new_child": self.widget.action_new_child,
            "edit_body": self.widget.action_edit_body,
            "delete": self.widget.action_delete,
            "toggle_fold": self.widget.action_toggle_expand,
            "expand_all": self.widget.action_expand_all,
        }.get(action)
        if fn is not None:
            fn()

    def on_window_focus(self) -> None:
        # Delegate keyboard focus to the inner tree widget (or the active
        # editor) so arrow keys / Enter reach it as soon as the window is
        # focused — without this the container holds focus and keys are lost.
        target = self._editor if self.is_editing and self._editor is not None else self.widget
        if target.is_mounted:
            target.focus()

    def on_unmount(self) -> None:
        if self.is_editing:
            self._commit_edit()

    async def on_tree_view_widget_edit_requested(
        self, event: TreeViewWidget.EditRequested
    ) -> None:
        await self._begin_edit(event.node, event.kind, event.line, event.col)

    def on_window_blur(self) -> None:
        # Leaving the tree window (e.g. clicking another window) commits the
        # in-progress edit so the body field doesn't linger.
        if self.is_editing:
            self._commit_edit()

    def on_tree_field_editor_tree_command(
        self, event: "TreeFieldEditor.TreeCommand"
    ) -> None:
        # Fold/unfold the node's children WITHOUT leaving the edit: the field
        # rows don't move, so the editor stays put.
        if event.name == "toggle_fold" and self.is_editing:
            node = self._editing_node
            if node is not None and node.is_branch:
                node.ensure_loaded()
                node.expanded = not node.expanded
                self.widget._rebuild_rows()
                self._sync_edit_layout()
            return
        # A tree command pressed while editing: commit the current field, put
        # the cursor back on its node, then run the action (which starts a new
        # edit on the inserted/opened field).
        node = self._editing_node
        self._commit_edit()
        if node is not None:
            self.widget._select_node(node)
        action = {
            "new_node": self.widget.action_new_node,
            "new_child": self.widget.action_new_child,
            "edit_body": self.widget.action_edit_body,
        }.get(event.name)
        if action is not None:
            action()

    def on_tree_view_widget_deleted(self, event: TreeViewWidget.Deleted) -> None:
        self.is_dirty = True

    def on_tree_view_widget_inserted(self, event: TreeViewWidget.Inserted) -> None:
        # Track new nodes so an empty one is dropped if the user cancels (Esc).
        # Dirty is set on commit only if the node ends up with content.
        if event.kind == "node":
            self._fresh_nodes.add(event.node)

    def _palette(self):
        for a in self.ancestors_with_self:
            pal = getattr(a, "palette", None)
            if pal is not None and hasattr(pal, "get"):
                return pal
        return None

    def _editor_palette(self, kind: str):
        """Editor palette for the field being edited: a bright in-text caret
        (distinct from the softer list-row cursor); the body additionally gets
        the muted text + tinted background."""
        pal = self._palette()
        if pal is None:
            return None
        base = pal.get("window.content")
        # Bright caret block: dark glyph on a near-white background.
        pal = pal.with_override(
            "editor.cursor", Style(fg=base.bg, bg=brighten(base.fg), bold=True)
        )
        if kind == "body":
            pal = pal.with_override(
                "window.content", Style(fg=dim_fg(base.fg), bg=tint_bg(base.bg))
            )
        return pal

    async def _begin_edit(
        self, node: TreeNode, kind: str = "body", line: int = 0, col: int = 0
    ) -> None:
        if self.is_editing:
            self._commit_edit()
        await self._editor_box.remove_children()  # ensure prior editor is gone
        self._editing_node = node
        self._editing_kind = kind
        if kind == "body":
            node.body_open = True
            self._orig_text = node.body or ""
        else:
            self._orig_text = node.label or ""
        self.widget.editing_node = node
        self.widget.editing_kind = kind
        buf = TextBuffer.from_string(self._orig_text)
        self._editor = TreeFieldEditor(
            buffer=buf, show_line_numbers=False, palette=self._editor_palette(kind)
        )
        await self._editor_box.mount(self._editor)
        # The thin █/░ scrollbar look is applied by EditorWidget.on_mount, which
        # TreeFieldEditor inherits — no per-instance assignment needed here.
        self._editor_box.add_class("editing")
        # Rebuild first so the field's rows exist, then park the tree cursor on
        # the field's first row (used by the boundary-escape flow).
        self.widget._rebuild_rows()
        self.widget.cursor = self.widget.field_row_index(node, kind)
        self._sync_edit_layout()
        # Place the caret where the click / navigation landed (not the start).
        row = max(0, min(line, buf.line_count - 1))
        buf.cursor_row = row
        buf.cursor_col = max(0, min(col, len(buf.lines[row])))
        self._editor._post_cursor_update()
        self._editor.focus()

    def on_editor_widget_buffer_modified(
        self, event: EditorWidget.BufferModified
    ) -> None:
        # Re-flow on every keystroke so the field height tracks the text and
        # the nodes below shift down/up accordingly.
        if self.is_editing:
            self._sync_edit_layout()

    def _sync_edit_layout(self) -> None:
        """Keep the reserved body rows AND the editor height equal to the live
        line count. The body sits in the tree's flow (nodes below shift down)
        and the inline editor overlays exactly those rows."""
        if self._editor is None or self._editing_node is None:
            return
        node = self._editing_node
        kind = self._editing_kind
        w = self.widget
        # Live-sync the model so flatten reserves one row per line, then place
        # and size the overlay to cover exactly those rows.
        text = "\n".join(self._editor.buffer.lines)
        if kind == "body":
            node.body = text
        else:
            node.label = text
        w._rebuild_rows()
        w._ensure_cursor_visible()
        self._position_editor()

    def _position_editor(self) -> None:
        """Place/size the overlay over the field's rows for the current scroll
        position. Re-run on wheel scroll so the editor tracks the node."""
        if self._editor is None or self._editing_node is None:
            return
        w = self.widget
        node, kind = self._editing_node, self._editing_kind
        n = max(1, len(self._editor.buffer.lines))
        row_idx = w.field_row_index(node, kind)
        screen_y = row_idx - w.row_offset                  # first line of the field
        indent = body_indent(w.header_depth(node))         # align under the label
        sb = 1 if w._scrollbar_geom()[0] else 0            # leave the tree's bar visible
        width = max(4, w.size.width - indent - sb)
        # Cap the height to the space left in the viewport so a body taller than
        # the window scrolls INSIDE the editor (a ScrollView) — the caret stays
        # visible instead of running off the bottom.
        avail = max(1, w.size.height - max(0, screen_y))
        # Reserve a row for the editor's own horizontal scrollbar when a line
        # overflows, so the bar sits below the text, not on the last line.
        longest = max((len(s) for s in self._editor.buffer.lines), default=0)
        usable = width - (1 if n > avail else 0)           # minus editor v-scrollbar
        extra = 1 if longest > usable else 0
        height = min(n + extra, avail)
        self._editor_box.styles.offset = (indent, screen_y)
        self._editor_box.styles.width = width
        self._editor_box.styles.height = height

    def _commit_edit(self, collapse: bool = True) -> None:
        if self._editing_node is None or self._editor is None:
            return
        node = self._editing_node
        kind = self._editing_kind
        new_text = "\n".join(self._editor.buffer.lines)
        changed = new_text != self._orig_text
        if kind == "body":
            node.body = new_text
            if collapse:
                # Esc / click-away fully removes the body field.
                node.body_open = False
        else:
            node.label = new_text
        # A freshly inserted node left completely empty is dropped (cancel).
        removed = False
        if node in self._fresh_nodes:
            self._fresh_nodes.discard(node)
            if not node.label and node.body is None and not node.children:
                parent = node.parent
                if parent is not None:
                    parent.children[:] = [c for c in parent.children if c is not node]
                    self.widget.selected.discard(node)
                    removed = True
        self.widget.editing_node = None
        if self._editor.is_mounted:
            self._editor.remove()
        self._editor = None
        self._editor_box.remove_class("editing")
        self._editing_node = None
        self.widget._rebuild_rows()
        if self.widget.is_mounted:
            self.widget.focus()
        if changed and not removed:
            self.is_dirty = True
            self.post_message(self.BodyEdited(node))

    async def on_tree_field_editor_cursor_escaped(
        self, event: TreeFieldEditor.CursorEscaped
    ) -> None:
        # Caret moved past the editor's top/bottom edge: commit and continue
        # editing straight into the adjacent field (label or body) so vertical
        # arrows flow through every editable line. Keep the caret column.
        w = self.widget
        first = w.cursor
        n = max(1, len(self._editor.buffer.lines)) if self._editor else 1
        col = self._editor.buffer.cursor_col if self._editor else 0
        self._commit_edit(collapse=False)
        target = (first - 1) if event.direction < 0 else (first + n)
        if 0 <= target < len(w.rows):
            row = w.rows[target]
            kind = "body" if isinstance(row, BodyRow) else "label"
            await self._begin_edit(row.node, kind, line=row.line_index, col=col)
        else:
            # Nothing beyond the boundary — stay in navigation.
            w.cursor = max(0, min(target, len(w.rows) - 1))
            w._ensure_cursor_visible()
            w.refresh()
            if w.is_mounted:
                w.focus()

    def on_key(self, event: events.Key) -> None:
        if self.is_editing and event.key == "escape":
            self._commit_edit()
            event.stop()

    def get_commands(self) -> list[WindowCommand]:
        return [
            WindowCommand(
                id="tree.edit", label="Edit snippet", hotkey="f4",
                handler=lambda: self.widget.action_edit(),
            ),
        ]
