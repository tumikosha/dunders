"""Map parsed JSON/YAML data to the tree model and back.

Mapping (spec variant 1): an object/array is a branch node whose children are
its keys/items; a scalar is a leaf whose ``body`` holds the editable value.
``node.data["kind"]`` records the container kind so the tree can be turned back
into Python data. Pure, Textual-free.
"""

from __future__ import annotations

import json
from typing import Any

from dunders.windowing.core.tree_model import TreeNode


def format_scalar(value: Any) -> str:
    """Render a scalar value as the editable body text (JSON-style literals)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    return str(value)


def parse_scalar(text: str) -> Any:
    """Infer a scalar from edited body text: int/float/bool/null when it parses
    as a JSON literal, otherwise the raw string."""
    try:
        value = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return text
    if isinstance(value, (int, float, bool, str)) or value is None:
        return value
    # A JSON object/array typed into a scalar body — keep it as plain text.
    return text


def _build(label: str, value: Any) -> TreeNode:
    if isinstance(value, dict):
        node = TreeNode(label=label, data={"kind": "dict"})
        for key, val in value.items():
            node.add_child(_build(str(key), val))
        return node
    if isinstance(value, list):
        node = TreeNode(label=label, data={"kind": "list"})
        for i, val in enumerate(value):
            node.add_child(_build(str(i), val))
        return node
    return TreeNode(label=label, body=format_scalar(value), data={"kind": "scalar"})


def tree_from_data(data: Any) -> TreeNode:
    """Build a forest root whose children are the document's top-level entries."""
    top = _build("<root>", data)
    # _build returns a node carrying the whole document; expose its children (or
    # itself for a scalar document) under a fresh hidden root.
    root = TreeNode(label="<root>", data={"kind": top.data["kind"]})
    if top.data["kind"] in ("dict", "list"):
        for child in top.children:
            root.add_child(child)
    else:
        root.add_child(top)
        root.data["kind"] = "scalar-doc"
    return root


def _value_of(node: TreeNode) -> Any:
    # Infer the value from the node's current structure so interactively-added
    # children are honoured: any node WITH children is a container (a list when
    # tagged so, otherwise an object); ``kind`` only disambiguates list-vs-dict
    # and preserves empty containers.
    kind = (node.data or {}).get("kind")
    if node.children:
        if kind == "list":
            return [_value_of(child) for child in node.children]
        return {child.label: _value_of(child) for child in node.children}
    if kind == "dict":
        return {}
    if kind == "list":
        return []
    if node.body is None:
        return None
    return parse_scalar(node.body)


def data_from_tree(root: TreeNode) -> Any:
    """Reconstruct the Python data structure from a tree built by
    ``tree_from_data`` (or edited from one)."""
    kind = (root.data or {}).get("kind", "dict")
    if kind == "list":
        return [_value_of(child) for child in root.children]
    if kind == "scalar-doc":
        return _value_of(root.children[0]) if root.children else None
    return {child.label: _value_of(child) for child in root.children}
