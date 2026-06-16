from textual.app import App, ComposeResult
from textual.geometry import Offset

from dunders.windowing.core.tree_model import TreeNode
from dunders.windowing.tree.widget import TreeViewWidget


def _tree():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a", body="l1\nl2"))
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
    async with app.run_test(size=(40, 20)):
        w = app.widget
        assert w.current_node.label == "a"


async def test_down_moves_through_body_lines():
    root = _tree()
    root.children[0].body_open = True  # 'a' shows 2 body rows
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        # rows: a(label), a-body0, a-body1, b  -> cursor moves one line at a time
        await pilot.press("down")
        assert w.current_node.label == "a"   # on 'a' body line now
        await pilot.press("down")
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
        # 'b' is at y=1 (a=0, b=1). Its toggle glyph is the column right after
        # the dot: gutter " ○▸ " -> toggle at x=2.
        await pilot.click(w, offset=Offset(2, 1))
        assert root.children[1].expanded is True


async def test_click_snippet_requests_edit():
    root = _tree()
    root.children[0].body_open = True  # 'a' body visible at y=1,2
    app = _App(root)
    messages = []

    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        original = w.post_message
        w.post_message = lambda m: (messages.append(m), original(m))[-1]  # capture
        await pilot.click(w, offset=Offset(6, 1))      # body row of 'a'
        assert any(type(m).__name__ == "EditRequested" for m in messages)


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
        await pilot.press("insert")        # mark 'b', move to 'b1'
        assert root.children[1] in w.selected
        assert len(w.selected) >= 2


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


async def test_insert_toggles_off():
    root = _tree()
    root.children[1].expanded = True
    app = _App(root)
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.press("insert")        # mark a, cursor -> b
        await pilot.press("up")            # back to a (plain move keeps marks)
        await pilot.press("insert")        # unmark a, cursor -> b
        assert root.children[0] not in w.selected


def test_tint_bg_lightens_dark_and_darkens_light():
    from dunders.windowing.tree.widget import tint_bg
    assert tint_bg("#262626") == "#3c3c3c"      # dark theme -> lighter band
    assert tint_bg("#eeeeee") < "#eeeeee"        # light theme -> darker band
    assert tint_bg(None) is None
    assert tint_bg("nothex") == "nothex"


async def test_click_requests_body_edit():
    root = _tree()
    app = _App(root)
    msgs = []
    async with app.run_test(size=(40, 20)) as pilot:
        w = app.widget
        original = w.post_message
        w.post_message = lambda m: (msgs.append(m), original(m))[-1]
        await pilot.click(w, offset=Offset(8, 0))  # click 'a' text -> body edit
        ev = [m for m in msgs if type(m).__name__ == "EditRequested"]
        assert ev and ev[0].kind == "body"


async def test_header_shows_tree_guides_and_dot():
    from dunders.windowing.core.tree_model import HeaderRow
    root = TreeNode(label="root")
    notes = root.add_child(TreeNode(label="notes"))
    todo = notes.add_child(TreeNode(label="todo", body="x"))   # has body -> filled dot
    todo.add_child(TreeNode(label="sub"))
    notes.add_child(TreeNode(label="idea"))                     # no body -> hollow dot
    root.add_child(TreeNode(label="last"))
    notes.expanded = True
    todo.expanded = True
    app = _App(root)
    async with app.run_test(size=(40, 20)):
        w = app.widget
        w._rebuild_rows()
        t = {r.node.label: w._header_text(r) for r in w.rows if isinstance(r, HeaderRow)}
        # filled dot only on nodes with a body; hollow otherwise
        assert "●" in t["todo"] and "○" not in t["todo"]
        assert "○" in t["idea"] and "●" not in t["idea"]
        assert "○" in t["notes"]                           # branch, no body -> hollow
        # top-level nodes carry no connector: dot at the left, branches drop from it
        assert t["notes"].startswith(" ○")
        assert t["last"].startswith(" ○")
        # children connect right under the parent's dot column
        assert t["todo"].startswith(" ├─●")               # not last child, has body
        assert t["idea"].startswith(" └─○")               # last child, no body
        # a deeper node continues the parent's vertical under its dot
        assert t["sub"].startswith(" │ ")


async def test_open_body_keeps_passing_branch_lines():
    from dunders.windowing.core.tree_model import BodyRow
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a"))             # branch
    x = a.add_child(TreeNode(label="x", body="L1\nL2")) # has body, not last
    a.add_child(TreeNode(label="y"))                    # sibling after x
    a.expanded = True
    x.body_open = True
    app = _App(root)
    async with app.run_test(size=(40, 20)):
        w = app.widget
        w._rebuild_rows()
        body_rows = [w._body_text(r) for r in w.rows if isinstance(r, BodyRow)]
        assert body_rows
        # x is not the last child of 'a' -> its connector vertical continues
        # through the body rows down to sibling 'y'.
        assert all("│" in line for line in body_rows)


async def test_label_display_modes():
    from dunders.windowing.core.tree_model import HeaderRow
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="L1\nL2\nL3"))
    root.add_child(TreeNode(label="x"))
    for mode, expected in (("all", 4), ("first", 2), ("inline", 2)):
        app = _App(root)
        app.widget = None
        async with app.run_test(size=(40, 12)):
            w = app.widget
            w.label_display = mode
            w._rebuild_rows()
            header_rows = [r for r in w.rows if isinstance(r, HeaderRow)]
            assert len(header_rows) == expected, mode
            if mode == "inline":
                assert "L1 L2 L3" in w._header_text(header_rows[0])
            if mode == "first":
                assert "L1" in w._header_text(header_rows[0])


async def test_delete_clears_body_then_deletes_node():
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a", body="b1\nb2"))
    root.add_child(TreeNode(label="b"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.widget
        await pilot.press("right")          # open a's body
        await pilot.press("down")           # cursor on a body line
        await pilot.press("delete")         # Del on body -> clear body, keep node
        assert a.has_body is False
        assert [n.label for n in root.children] == ["a", "b"]
        # cursor now on 'a' label; Del deletes the node
        await pilot.press("delete")
        assert [n.label for n in root.children] == ["b"]
        assert w.current_node.label == "b"


async def test_body_rows_have_thin_left_border():
    from dunders.windowing.core.tree_model import BodyRow
    root = TreeNode(label="root")
    n = root.add_child(TreeNode(label="n", body="one\ntwo"))
    n.body_open = True
    app = _App(root)
    async with app.run_test(size=(40, 12)):
        w = app.widget
        w._rebuild_rows()
        body_rows = [i for i, r in enumerate(w.rows) if isinstance(r, BodyRow)]
        assert body_rows
        for i in body_rows:
            assert "▏" in w.render_line(i).text
        # label rows carry no border
        assert "▏" not in w.render_line(0).text


async def test_shift_selection_shrinks_when_reversing():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    root.add_child(TreeNode(label="b"))
    root.add_child(TreeNode(label="c"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.press("shift+down")            # a..b
        await pilot.press("shift+down")            # a..c
        assert {n.label for n in w.selected} == {"a", "b", "c"}
        await pilot.press("shift+up")              # reversing shrinks: a..b
        assert {n.label for n in w.selected} == {"a", "b"}
        await pilot.press("shift+up")              # a only (anchor)
        assert {n.label for n in w.selected} == {"a"}


async def test_escape_clears_selection():
    root = TreeNode(label="root")
    for x in "abc":
        root.add_child(TreeNode(label=x))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.widget
        w._rebuild_rows()
        await pilot.press("shift+down")
        await pilot.press("shift+down")
        assert w.selected
        await pilot.press("escape")            # clears the whole selection
        assert w.selected == set()


async def test_shift_over_marked_nodes_deselects():
    root = TreeNode(label="root")
    for x in "abcd":
        root.add_child(TreeNode(label=x))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.widget
        w._rebuild_rows()
        # select a..c
        await pilot.press("shift+down")
        await pilot.press("shift+down")
        assert {n.label for n in w.selected} == {"a", "b", "c"}
        # plain nav ends the session; restart ON a marked node -> deselect mode
        await pilot.press("down")
        w.cursor = 0                       # back on 'a' (marked)
        await pilot.press("shift+down")    # deselect a..b
        assert {n.label for n in w.selected} == {"c"}
        await pilot.press("shift+down")    # deselect a..c
        assert w.selected == set()


async def test_expand_all_opens_bodies_too():
    root = TreeNode(label="root")
    a = root.add_child(TreeNode(label="a", body="A1\nA2"))   # leaf with body
    folder = root.add_child(TreeNode(label="folder", body="F"))  # branch + body
    c = folder.add_child(TreeNode(label="c", body="C"))
    app = _App(root)
    async with app.run_test(size=(40, 12)) as pilot:
        w = app.widget
        w.action_expand_all()
        await pilot.pause()
        assert a.body_open and folder.body_open and folder.expanded and c.body_open
        w.action_collapse_all()
        await pilot.pause()
        assert not a.body_open and not folder.expanded and not c.body_open


async def test_scrollbar_appears_when_overflowing_and_drags():
    root = TreeNode(label="root")
    for i in range(30):
        root.add_child(TreeNode(label=f"n{i}"))
    app = _App(root)
    async with app.run_test(size=(20, 10)) as pilot:   # 10 rows, 30 nodes
        w = app.widget
        w._rebuild_rows()
        await pilot.pause()
        active, _start, size = w._scrollbar_geom()
        assert active and size >= 1
        col = "".join(w.render_line(y).text[-1] for y in range(w.size.height))
        assert "▌" in col and "▏" in col          # thumb + track in last column
        # dragging the thumb to the bottom scrolls to the end
        w._sb_scroll_to(w.size.height - 1)
        assert w.row_offset == len(w.rows) - w.size.height


async def test_no_scrollbar_when_content_fits():
    root = TreeNode(label="root")
    root.add_child(TreeNode(label="a"))
    app = _App(root)
    async with app.run_test(size=(20, 10)) as pilot:
        await pilot.pause()
        assert app.widget._scrollbar_geom()[0] is False
