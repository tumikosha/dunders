from __future__ import annotations

from dataclasses import dataclass

from dunders.windowing.core.fold_engine import FoldRegion, FoldRule


@dataclass
class IndentFoldRule(FoldRule):
    start_label: str = "__INDENT__"
    end_label: str = "__INDENT__"
    placeholder: str = " ⋯"
    nested: bool = True
    priority: int = 1
    blank_lines_break: bool = False
    min_lines: int = 2
    tab_size: int = 4


def _visual_indent(line: str, tab_size: int) -> int | None:
    col = 0
    for ch in line:
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += tab_size - (col % tab_size)
        else:
            return col
    return None


def scan_indent_regions(
    lines: list[str], rule: IndentFoldRule
) -> list[FoldRegion]:
    indents: list[int | None] = [
        _visual_indent(line, rule.tab_size) for line in lines
    ]

    stack: list[list[int]] = []
    regions: list[FoldRegion] = []
    prev_content_row: int | None = None

    def close_to_indent(current_indent: int) -> None:
        while stack and stack[-1][1] >= current_indent:
            header_row, _header_indent, last_content_row = stack.pop()
            regions.append(
                FoldRegion(
                    start_row=header_row,
                    start_col=len(lines[header_row]),
                    end_row=last_content_row,
                    end_col=max(0, len(lines[last_content_row]) - 1),
                    rule=rule,
                )
            )

    for i, indent in enumerate(indents):
        if indent is None:
            if rule.blank_lines_break:
                close_to_indent(0)
                prev_content_row = None
            continue

        close_to_indent(indent)

        if prev_content_row is not None:
            prev_indent = indents[prev_content_row]
            if prev_indent is not None and prev_indent < indent:
                stack.append([prev_content_row, prev_indent, i])

        for entry in stack:
            entry[2] = i

        prev_content_row = i

    close_to_indent(-1)

    filtered = [
        r for r in regions if (r.end_row - r.start_row) >= rule.min_lines
    ]
    return filtered
