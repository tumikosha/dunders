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

    __hash__ = object.__hash__

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


@dataclass(frozen=True)
class HeaderRow:
    """A visible row showing one line of a node's (possibly multi-line) label.

    ``line_index`` 0 is the primary line — it carries the guides, dot and
    expand arrow; lines > 0 are label continuation lines.
    """
    node: TreeNode
    depth: int
    line_index: int = 0


@dataclass(frozen=True)
class BodyRow:
    """A visible row showing one line of a node's snippet body."""
    node: TreeNode
    depth: int
    line_index: int


Row = HeaderRow | BodyRow


def label_lines(node: TreeNode) -> list[str]:
    """The node's label split into raw lines (always at least one)."""
    return (node.label or "").split("\n") or [""]


def visible_label_lines(node: TreeNode, mode: str = "all") -> list[str]:
    """Label lines as displayed in the tree. ``mode``:
    ``"all"`` — every line; ``"first"`` — only the first line;
    ``"inline"`` — all lines joined with spaces onto one line."""
    lines = label_lines(node)
    if mode == "first":
        return lines[:1]
    if mode == "inline":
        return [" ".join(lines)]
    return lines


def flatten_visible(
    root: TreeNode, label_mode: str = "all", expand_label: TreeNode | None = None
) -> list[Row]:
    """Walk the model honouring ``expanded``/``body_open`` and return the flat
    list of visible rows. Pure: does NOT trigger lazy loading (the widget calls
    ``ensure_loaded`` before expanding). ``expand_label`` forces that node's
    label to show all lines (used while its label is being edited)."""
    rows: list[Row] = []

    def label_count(node: TreeNode) -> int:
        if node is expand_label:
            return len(label_lines(node))
        return len(visible_label_lines(node, label_mode))

    def walk(node: TreeNode, depth: int) -> None:
        for i in range(label_count(node)):
            rows.append(HeaderRow(node, depth, i))
        if node.body_open and node.body is not None:
            for i in range(len(node.body.split("\n"))):
                rows.append(BodyRow(node, depth, i))
        if node.expanded:
            for child in node.children:
                walk(child, depth + 1)

    for child in root.children:
        walk(child, 0)
    return rows


def header_nodes(rows: list[Row]) -> list[TreeNode]:
    """One node per node, in visible order (only the primary label line)."""
    return [r.node for r in rows if isinstance(r, HeaderRow) and r.line_index == 0]


def _index_by_identity(headers: list[TreeNode], node: TreeNode) -> int:
    for i, n in enumerate(headers):
        if n is node:
            return i
    raise ValueError("node not in visible headers")


def nodes_in_range(rows: list[Row], a: TreeNode, b: TreeNode) -> list[TreeNode]:
    """Header nodes between ``a`` and ``b`` inclusive, in visible order,
    regardless of which comes first."""
    headers = header_nodes(rows)
    ia = _index_by_identity(headers, a)
    ib = _index_by_identity(headers, b)
    lo, hi = sorted((ia, ib))
    return headers[lo : hi + 1]
