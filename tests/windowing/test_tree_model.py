from dunders.windowing.core.tree_model import (
    TreeNode,
    HeaderRow, BodyRow, flatten_visible,
    header_nodes, nodes_in_range,
)


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
