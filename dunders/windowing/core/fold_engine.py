from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FoldRule:
    start_label: str
    end_label: str
    placeholder: str
    nested: bool = True
    priority: int = 0


class FoldRegistry:
    def __init__(self) -> None:
        self._rules: list[FoldRule] = []

    @property
    def rules(self) -> list[FoldRule]:
        return sorted(self._rules, key=lambda r: r.priority, reverse=True)

    def add_rule(self, rule: FoldRule) -> None:
        self._rules.append(rule)

    def remove_rule(self, rule: FoldRule) -> None:
        self._rules.remove(rule)


@dataclass
class FoldRegion:
    start_row: int
    start_col: int
    end_row: int
    end_col: int
    rule: FoldRule
    collapsed: bool = False

    @property
    def is_block(self) -> bool:
        return self.start_row != self.end_row


def effective_placeholder(region: FoldRegion, show_line_count: bool) -> str:
    if not show_line_count or not region.is_block:
        return region.rule.placeholder
    n = region.end_row - region.start_row
    return f"{region.rule.placeholder}  ({n} lines)"


class FoldEngine:
    def __init__(self, registry: FoldRegistry) -> None:
        self.registry = registry

    def scan(self, lines: list[str]) -> list[FoldRegion]:
        regions: list[FoldRegion] = []
        for rule in self.registry.rules:
            self._scan_rule(lines, rule, regions)
        return regions

    def _scan_rule(
        self, lines: list[str], rule: FoldRule, regions: list[FoldRegion]
    ) -> None:
        from dunders.windowing.core.indent_fold import IndentFoldRule, scan_indent_regions

        if isinstance(rule, IndentFoldRule):
            regions.extend(scan_indent_regions(lines, rule))
            return

        stack: list[tuple[int, int]] = []
        for row_idx, line in enumerate(lines):
            col = 0
            while col < len(line):
                start_pos = line.find(rule.start_label, col)
                end_pos = line.find(rule.end_label, col)

                if start_pos == -1 and end_pos == -1:
                    break

                if start_pos != -1 and (end_pos == -1 or start_pos < end_pos):
                    stack.append((row_idx, start_pos))
                    col = start_pos + len(rule.start_label)
                elif end_pos != -1:
                    if stack:
                        sr, sc = stack.pop()
                        region = FoldRegion(
                            start_row=sr,
                            start_col=sc,
                            end_row=row_idx,
                            end_col=end_pos + len(rule.end_label) - 1,
                            rule=rule,
                        )
                        regions.append(region)
                    col = end_pos + len(rule.end_label)
                else:
                    break

    def toggle_fold(self, region: FoldRegion) -> None:
        region.collapsed = not region.collapsed

    def render_lines(
        self, lines: list[str], regions: list[FoldRegion],
        show_line_count: bool = False,
    ) -> list[str]:
        result, _ = self.render_lines_with_map(
            lines, regions, show_line_count=show_line_count
        )
        return result

    def render_lines_with_map(
        self, lines: list[str], regions: list[FoldRegion],
        show_line_count: bool = False,
    ) -> tuple[list[str], list[int]]:
        """Returns (rendered_lines, line_map) where line_map[rendered_idx] = buffer_row."""
        collapsed = [r for r in regions if r.collapsed]
        if not collapsed:
            return list(lines), list(range(len(lines)))

        # Build rendered lines and line map together
        collapsed_blocks = sorted(
            [r for r in collapsed if r.is_block],
            key=lambda r: r.start_row,
        )
        collapsed_inlines = [r for r in collapsed if not r.is_block]

        # Find which buffer rows are hidden by block folds
        hidden_rows: set[int] = set()
        block_start_rows: dict[int, FoldRegion] = {}
        for region in collapsed_blocks:
            block_start_rows[region.start_row] = region
            for r in range(region.start_row + 1, region.end_row + 1):
                hidden_rows.add(r)

        result: list[str] = []
        line_map: list[int] = []

        for buf_row, line in enumerate(lines):
            if buf_row in hidden_rows:
                continue

            # Apply block fold placeholder
            if buf_row in block_start_rows:
                region = block_start_rows[buf_row]
                line = line[: region.start_col] + effective_placeholder(
                    region, show_line_count
                )

            # Apply inline folds (sorted reverse by col to avoid index shift)
            inline_on_row = sorted(
                [r for r in collapsed_inlines if r.start_row == buf_row],
                key=lambda r: r.start_col,
                reverse=True,
            )
            for region in inline_on_row:
                before = line[: region.start_col]
                after = line[region.end_col + 1 :]
                line = before + effective_placeholder(region, show_line_count) + after

            result.append(line)
            line_map.append(buf_row)

        return result, line_map
