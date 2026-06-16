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
    async with app.run_test(size=(50, 20)):
        assert app.content.window_title == "Tree"


async def test_edit_cycle_writes_body_and_marks_dirty():
    from textual.geometry import Offset
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.click(c.widget, offset=Offset(8, 0))   # click 'a' -> edit body
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing is True
        await pilot.press("!")
        await pilot.press("escape")
        await pilot.pause()
        assert c.is_editing is False
        assert "!" in root.children[0].body
        assert c.is_dirty is True


async def test_enter_on_label_edits_label():
    root = _tree()
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("enter")          # cursor on 'a' label line -> edit label
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing is True
        assert c._editing_kind == "label"
        await pilot.press("!")
        await pilot.press("escape")
        await pilot.pause()
        assert "!" in root.children[0].label


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


async def test_reentrant_edit_does_not_stack_editors():
    from dunders.windowing.editor.widget import EditorWidget
    root = _tree()
    root.children[0].add_child(TreeNode(label="a2", body="second"))
    root.children[0].expanded = True
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("enter")          # edit 'a'
        await pilot.pause()
        # directly request edit of the child snippet (re-entrant)
        c.post_message(c.widget.EditRequested(root.children[0].children[0]))
        await pilot.pause()
        await pilot.pause()
        editors = c._editor_box.query(EditorWidget)
        assert len(editors) == 1


def test_public_reexport():
    import dunders.windowing as W
    assert hasattr(W, "TreeContent")
    assert hasattr(W, "TreeViewWidget")
    assert hasattr(W, "TreeNode")


def test_build_demo_tree_has_lazy_branch_and_snippets():
    from dunders.windowing.demo.contents import build_demo_tree
    root = build_demo_tree()
    labels = [c.label for c in root.children]
    assert labels  # non-empty
    assert any(c.has_body for c in root.children) or any(
        gc.has_body for c in root.children for gc in c.children
    )
    assert any(c.loader is not None for c in root.children)


async def test_on_window_focus_delegates_to_tree_widget():
    app = _App(_tree())
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.on_window_focus()
        await pilot.pause()
        assert c.widget.has_focus


async def test_editor_sized_to_body_and_collapses_on_close():
    from textual.geometry import Offset
    root = _tree()  # node 'a' body = "hello\nworld" (2 lines)
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.click(c.widget, offset=Offset(8, 0))   # click 'a' -> edit body
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        # editor covers exactly the body's line count — no spill over siblings
        assert c._editor.size.height == 2
        await pilot.press("escape")
        await pilot.pause()
        assert not c.is_editing
        # closing fully removes the body field (collapsed back to a header)
        assert root.children[0].body_open is False


async def test_editing_keeps_following_nodes_below_body_and_grows():
    from dunders.windowing.core.tree_model import HeaderRow
    root = _tree()  # 'a' body = 2 lines, then 'b'
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        from textual.geometry import Offset
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.click(w, offset=Offset(8, 0))   # click 'a' -> edit body
        await pilot.pause()
        await pilot.pause()

        def hidx(label):
            return [i for i, r in enumerate(w.rows)
                    if isinstance(r, HeaderRow) and r.node.label == label][0]

        # body occupies its own rows in the flow; 'b' sits below them
        assert c._editor.size.height == 2
        assert hidx("b") == 3                   # a-header + 2 body rows, then b
        # growing the text grows the field and pushes 'b' further down
        await pilot.press("ctrl+end")
        await pilot.press("enter")
        await pilot.press("X")
        await pilot.pause()
        await pilot.pause()
        assert c._editor.size.height == 3
        assert hidx("b") == 4


async def test_clicking_elsewhere_commits_and_removes_editor():
    from textual.geometry import Offset
    root = _tree()  # 'a' (2-line body), 'b'
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.click(c.widget, offset=Offset(8, 0))   # click 'a' -> edit body
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        await pilot.press("!")                      # modify 'a' body
        # rows while editing: a, a-body, a-body, b  -> 'b' header at y=3
        await pilot.click(c.widget, offset=Offset(8, 3))
        await pilot.pause()
        await pilot.pause()
        # clicking another node commits the current edit (no lingering overlay)
        # and starts editing that node instead.
        assert c._editing_node is not root.children[0]   # 'a' was committed
        assert "!" in (root.children[0].body or "")


async def test_click_toggles_snippet_open_then_closed():
    from textual.geometry import Offset
    root = _tree()  # 'a' (body), 'b'
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        # first click on 'a' header (y=0) opens it for editing
        await pilot.click(c.widget, offset=Offset(8, 0))
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        assert root.children[0].body_open is True
        # second click on the same header closes it (toggle off)
        await pilot.click(c.widget, offset=Offset(8, 0))
        await pilot.pause()
        await pilot.pause()
        assert not c.is_editing
        assert root.children[0].body_open is False


async def test_editor_indented_under_node_label():
    from dunders.windowing.tree.widget import body_indent
    root = _tree()  # top-level 'a' (depth 0) with body
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        off = c._editor_box.styles.offset
        assert int(off.x.value) == body_indent(0)   # aligned under the label


async def test_cursor_escapes_editor_into_adjacent_field():
    from textual.geometry import Offset
    root = _tree()  # 'a' body = "hello\nworld" (2 lines), then 'b'
    app = _App(root)
    async with app.run_test(size=(50, 20)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.click(w, offset=Offset(8, 0))   # edit 'a' body (caret line 0)
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and c._editing_kind == "body"
        # Up past the top -> continue editing 'a' label (no drop to navigation)
        await pilot.press("up")
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        assert (c._editing_node, c._editing_kind) == (root.children[0], "label")
        # Down -> back into 'a' body; Down x2 -> escape bottom into 'b' label
        await pilot.press("down")                   # into body line 0
        await pilot.pause()
        await pilot.press("down")                   # body line 1
        await pilot.press("down")                   # past bottom -> 'b' label
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        assert (c._editing_node, c._editing_kind) == (root.children[1], "label")


async def test_editor_tracks_tree_scroll():
    from textual.geometry import Offset
    root = TreeNode(label="root")
    for i in range(20):
        root.add_child(TreeNode(label=f"n{i}", body=f"body {i}"))
    app = _App(root)
    async with app.run_test(size=(40, 10)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.click(w, offset=Offset(8, 2))   # edit a node's body
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        ri = w.field_row_index(c._editing_node, "body")
        assert float(c._editor_box.styles.offset.y.value) == ri - w.row_offset
        # wheel-scroll the tree -> the overlay tracks its node, not stays put
        w.on_mouse_scroll_down(type("E", (), {"stop": lambda s: None})())
        await pilot.pause()
        assert w.row_offset > 0
        assert float(c._editor_box.styles.offset.y.value) == ri - w.row_offset


async def test_large_body_scrolls_inside_editor():
    from textual.geometry import Offset
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="big", body="\n".join(f"line {i}" for i in range(30))))
    app = _App(root)
    async with app.run_test(size=(40, 10)) as pilot:   # window shorter than body
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.click(w, offset=Offset(8, 0))   # edit the big body
        await pilot.pause()
        await pilot.pause()
        ed = c._editor
        # editor height is capped to the viewport so it scrolls internally
        assert ed.size.height < ed.buffer.line_count
        for _ in range(25):
            await pilot.press("down")
        await pilot.pause()
        # caret stayed within the editor's scrolled viewport
        assert ed.scroll_offset.y <= ed.buffer.cursor_row < ed.scroll_offset.y + ed.size.height


async def test_edit_caret_lands_on_navigated_body_line():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="n", body="aa\nbb\ncc\ndd\nee"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.press("right")          # open body
        await pilot.press("down")           # body line 0
        await pilot.press("down")           # body line 1
        await pilot.press("down")           # body line 2 ('cc')
        await pilot.press("enter")          # edit -> caret should be on line 2
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and c._editing_kind == "body"
        assert c._editor.buffer.cursor_row == 2


async def test_click_caret_lands_on_clicked_body_line():
    from textual.geometry import Offset
    from dunders.windowing.core.tree_model import BodyRow
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="n", body="aa\nbb\ncc\ndd"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.press("right")          # open body
        ri = [i for i, r in enumerate(w.rows)
              if isinstance(r, BodyRow) and r.line_index == 2][0]
        await pilot.click(w, offset=Offset(9, ri - w.row_offset))   # click 'cc'
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing
        assert c._editor.buffer.cursor_row == 2


async def test_ctrl_n_inserts_sibling_and_edits_label():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    root.add_child(TreeNode(label="b"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+n")          # cursor on 'a' -> new sibling after
        await pilot.pause()
        await pilot.pause()
        assert [n.label for n in root.children] == ["a", "", "b"]
        assert c.is_editing and c._editing_kind == "label"
        await pilot.press("z")
        await pilot.press("escape")
        await pilot.pause()
        assert [n.label for n in root.children] == ["a", "z", "b"]
        assert c.is_dirty                       # dirty once the node has content


async def test_ctrl_e_adds_and_edits_body():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))      # no body
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+e")
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and c._editing_kind == "body"
        assert root.children[0].has_body
        await pilot.press("h")
        await pilot.press("escape")
        await pilot.pause()
        assert root.children[0].body == "h"


async def test_ctrl_right_inserts_child():
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+right")      # child of 'a'
        await pilot.pause()
        await pilot.pause()
        assert a.expanded and len(a.children) == 1
        assert c.is_editing and c._editing_kind == "label"
        await pilot.press("k")
        await pilot.press("escape")
        await pilot.pause()
        assert [n.label for n in a.children] == ["k"]


async def test_empty_new_node_is_dropped_on_cancel():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    root.add_child(TreeNode(label="b"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("ctrl+n")          # inserts empty node
        await pilot.pause()
        await pilot.pause()
        assert len(root.children) == 3
        await pilot.press("escape")          # cancel without typing
        await pilot.pause()
        assert [n.label for n in root.children] == ["a", "b"]   # dropped
        assert c.is_dirty is False


async def test_tree_commands_work_inside_editor():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    root.add_child(TreeNode(label="b"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        # editing 'a' label, Ctrl+N commits and starts a new sibling
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+n")
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and c._editing_kind == "label"
        assert [n.label for n in root.children] == ["a", "", "b"]
        # Ctrl+E from a label edit opens the body of that node
        await pilot.press("q")              # name the new node so it survives
        await pilot.press("ctrl+e")
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and c._editing_kind == "body"
        # Ctrl+Right inserts a child of the current node
        await pilot.press("escape")
        await pilot.pause()
        w.cursor = [i for i, r in enumerate(w.rows) if r.node.label == "b"][0]
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("ctrl+right")
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and c._editing_node.parent.label == "b"


async def test_label_and_body_have_distinct_colours():
    from dunders.windowing.core.tree_model import HeaderRow, BodyRow
    from dunders.windowing.desktop import Desktop
    from dunders.windowing.helpers import make_window

    class DApp(App):
        def __init__(self, root):
            super().__init__()
            self._root = root
            self.content = None
        def compose(self):
            self.desktop = Desktop(theme_name="modern_dark")
            yield self.desktop
        def on_mount(self):
            self.content = TreeContent(self._root, title="T")
            w = make_window(self.content, title="T", position=(2, 2), size=(40, 12))
            self.desktop.add_window(w)
            self.desktop.focus_window(w)

    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a", body="bb"))
    app = DApp(root)
    async with app.run_test(size=(50, 16)) as pilot:
        await pilot.pause()
        await pilot.pause()
        w = app.content.widget
        root.children[0].body_open = True
        w._rebuild_rows()
        label_row = next(r for r in w.rows if isinstance(r, HeaderRow))
        body_row = next(r for r in w.rows if isinstance(r, BodyRow))
        ls = w._field_style(label_row)
        bs = w._field_style(body_row)
        assert ls.color != bs.color            # label vs body text colour
        assert bs.bgcolor is not None and ls.bgcolor is None   # body tinted, label not


async def test_text_caret_brighter_than_list_cursor():
    from dunders.windowing.desktop import Desktop
    from dunders.windowing.helpers import make_window

    class DApp(App):
        def __init__(self, root):
            super().__init__()
            self._root = root
            self.content = None
        def compose(self):
            self.desktop = Desktop(theme_name="modern_dark")
            yield self.desktop
        def on_mount(self):
            self.content = TreeContent(self._root, title="T")
            w = make_window(self.content, title="T", position=(2, 2), size=(40, 12))
            self.desktop.add_window(w)
            self.desktop.focus_window(w)

    def lum(hexcol):
        h = hexcol.lstrip("#")
        return sum(int(h[i:i+2], 16) for i in (0, 2, 4))

    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a", body="bb"))
    app = DApp(root)
    async with app.run_test(size=(50, 16)) as pilot:
        await pilot.pause()
        await pilot.pause()
        c = app.content
        list_cur = c.widget._cursor_style()                 # navigation row cursor
        caret = c._editor_palette("body").get("editor.cursor")  # in-text caret
        # different appearance, and the caret block is brighter
        assert str(list_cur.bgcolor) != str(caret.bg)
        assert lum(caret.bg) > lum(str(list_cur.bgcolor.triplet.hex))


async def test_ctrl_right_bracket_toggles_fold_while_editing():
    root = TreeNode(label="root")
    parent = root.add_child(TreeNode(label="parent"))
    parent.add_child(TreeNode(label="c1"))
    parent.add_child(TreeNode(label="c2"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await pilot.press("enter")          # edit 'parent' label
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and not parent.expanded
        await pilot.press("ctrl+right_square_bracket")   # unfold without leaving the edit
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and parent.expanded
        await pilot.press("ctrl+right_square_bracket")   # fold again
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and not parent.expanded


async def test_action_bar_buttons_run_operations():
    from textual.geometry import Offset
    from dunders.windowing.tree.content import TreeActionBar
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    root.add_child(TreeNode(label="b"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        await pilot.pause()
        await pilot.pause()
        bar = app.query_one(TreeActionBar)
        spans = bar._cells()
        gx = next(s[0] for s in spans if s[2] == "new_node")
        await pilot.click(bar, offset=Offset(gx, 0))      # ⊕ new sibling
        await pilot.pause()
        await pilot.pause()
        assert c.is_editing and len(root.children) == 3
        await pilot.press("z")
        await pilot.press("escape")
        await pilot.pause()
        assert [n.label for n in root.children] == ["a", "z", "b"]
        dx = next(s[0] for s in spans if s[2] == "delete")
        await pilot.click(bar, offset=Offset(dx, 0))      # ✕ delete current
        await pilot.pause()
        await pilot.pause()
        assert [n.label for n in root.children] == ["a", "b"]


async def test_action_bar_hover_highlights_and_tooltips():
    from textual.geometry import Offset
    from dunders.windowing.tree.content import TreeActionBar
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        await pilot.pause()
        bar = app.query_one(TreeActionBar)
        cells = bar._cells()
        gx = next(s[0] for s in cells if s[2] == "new_child")
        await pilot.hover(bar, offset=Offset(gx + 1, 0))
        await pilot.pause()
        assert bar.tooltip == "New child (Ctrl+T)"
        # the hovered button renders reverse-highlighted
        strip = bar.render_line(0)
        reversed_glyphs = [s.text for s in strip if getattr(s.style, "reverse", False)]
        assert any("↳" in g for g in reversed_glyphs)


async def test_action_bar_unfold_all():
    from textual.geometry import Offset
    from dunders.windowing.core.tree_model import HeaderRow
    from dunders.windowing.tree.content import TreeActionBar
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a"))
    a.add_child(TreeNode(label="a1"))
    b = root.add_child(TreeNode(label="b"))
    b.add_child(TreeNode(label="b1"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.content.widget
        w.focus()
        await pilot.pause()
        await pilot.pause()
        bar = app.query_one(TreeActionBar)
        gx = next(s[0] for s in bar._cells() if s[2] == "expand_all")
        await pilot.click(bar, offset=Offset(gx, 0))
        await pilot.pause()
        labels = [r.node.label for r in w.rows if isinstance(r, HeaderRow)]
        assert labels == ["a", "a1", "b", "b1"]


async def test_editor_leaves_room_for_tree_scrollbar_and_h_bar():
    root = TreeNode(label="root")
    longn = TreeNode(label="k", body="x" * 200)        # one very long line
    root.add_child(longn)
    for i in range(30):                                 # force the tree scrollbar
        root.add_child(TreeNode(label=f"n{i}"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        w = c.widget
        w.focus()
        w._rebuild_rows()
        await pilot.pause()
        assert w._scrollbar_geom()[0] is True
        await c._begin_edit(longn, "body", 0, 0)
        await pilot.pause()
        await pilot.pause()
        box = c._editor_box
        indent = int(box.styles.offset.x.value)
        width = int(box.styles.width.value)
        # right edge stops before the last column (the tree's scrollbar)
        assert indent + width <= w.size.width - 1
        # an extra row is reserved so the horizontal bar isn't on the text line
        assert int(box.styles.height.value) == 2
        assert c._editor.show_horizontal_scrollbar is True


async def test_short_body_reserves_no_h_bar_row():
    root = TreeNode(label="root")
    s = TreeNode(label="s", body="hi")
    root.add_child(s)
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        c = app.content
        c.widget.focus()
        await pilot.pause()
        await c._begin_edit(s, "body", 0, 0)
        await pilot.pause()
        await pilot.pause()
        assert int(c._editor_box.styles.height.value) == 1
        assert c._editor.show_horizontal_scrollbar is False
