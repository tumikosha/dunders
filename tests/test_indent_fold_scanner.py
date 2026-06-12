from dunders.windowing.core.fold_engine import FoldRegion
from dunders.windowing.core.indent_fold import IndentFoldRule, scan_indent_regions


def _rule(**overrides) -> IndentFoldRule:
    params = dict(placeholder=" ⋯", priority=1, min_lines=2,
                  blank_lines_break=False, tab_size=4)
    params.update(overrides)
    return IndentFoldRule(**params)


class TestScanIndentRegions:
    def test_empty_lines(self):
        assert scan_indent_regions([], _rule()) == []

    def test_no_indentation(self):
        lines = ["a = 1", "b = 2", "c = 3"]
        assert scan_indent_regions(lines, _rule()) == []

    def test_single_level_block(self):
        lines = [
            "def foo():",
            "    a = 1",
            "    b = 2",
        ]
        regions = scan_indent_regions(lines, _rule())
        assert len(regions) == 1
        r = regions[0]
        assert r.start_row == 0
        assert r.end_row == 2
        assert r.start_col == len(lines[0])
        assert r.end_col == len(lines[2]) - 1
        assert isinstance(r.rule, IndentFoldRule)

    def test_nested_blocks(self):
        lines = [
            "def foo():",
            "    if x:",
            "        a = 1",
            "        b = 2",
            "    return a",
        ]
        regions = sorted(scan_indent_regions(lines, _rule()),
                         key=lambda r: (r.start_row, -r.end_row))
        assert len(regions) == 2
        outer, inner = regions
        assert outer.start_row == 0 and outer.end_row == 4
        assert inner.start_row == 1 and inner.end_row == 3

    def test_blank_lines_transparent(self):
        lines = [
            "def foo():",
            "    a = 1",
            "",
            "    b = 2",
            "def bar():",
            "    c = 3",
            "    d = 4",
        ]
        regions = scan_indent_regions(lines, _rule(blank_lines_break=False))
        regions = sorted(regions, key=lambda r: r.start_row)
        assert len(regions) == 2
        foo, bar = regions
        assert foo.start_row == 0 and foo.end_row == 3
        assert bar.start_row == 4 and bar.end_row == 6

    def test_blank_lines_break(self):
        lines = [
            "def foo():",
            "    a = 1",
            "    b = 2",
            "",
            "    c = 3",
        ]
        regions = scan_indent_regions(lines, _rule(blank_lines_break=True))
        assert len(regions) == 1
        r = regions[0]
        assert r.start_row == 0 and r.end_row == 2

    def test_trailing_blank_not_in_end_row(self):
        lines = [
            "def foo():",
            "    a = 1",
            "    b = 2",
            "",
            "",
            "def bar():",
            "    c = 3",
            "    d = 4",
        ]
        regions = scan_indent_regions(lines, _rule(blank_lines_break=False))
        foo = next(r for r in regions if r.start_row == 0)
        assert foo.end_row == 2

    def test_tab_expansion(self):
        lines = [
            "def foo():",
            "\ta = 1",
            "\tb = 2",
        ]
        regions = scan_indent_regions(lines, _rule(tab_size=4))
        assert len(regions) == 1
        r = regions[0]
        assert r.start_row == 0 and r.end_row == 2

    def test_mixed_tab_and_spaces_same_width(self):
        lines = [
            "def foo():",
            "\ta = 1",
            "    b = 2",
        ]
        regions = scan_indent_regions(lines, _rule(tab_size=4))
        assert len(regions) == 1
        assert regions[0].end_row == 2

    def test_min_lines_filter_drops_single_line_body(self):
        lines = [
            "if x:",
            "    return",
            "a = 1",
        ]
        assert scan_indent_regions(lines, _rule(min_lines=2)) == []

    def test_min_lines_1_keeps_single_line_body(self):
        lines = [
            "if x:",
            "    return",
            "a = 1",
        ]
        regions = scan_indent_regions(lines, _rule(min_lines=1))
        assert len(regions) == 1
        assert regions[0].start_row == 0 and regions[0].end_row == 1

    def test_block_at_eof(self):
        lines = [
            "def foo():",
            "    a = 1",
            "    b = 2",
        ]
        regions = scan_indent_regions(lines, _rule())
        assert len(regions) == 1
        assert regions[0].end_row == 2

    def test_dedent_closes_block(self):
        lines = [
            "def foo():",
            "    a = 1",
            "    b = 2",
            "c = 3",
        ]
        regions = scan_indent_regions(lines, _rule())
        assert len(regions) == 1
        assert regions[0].end_row == 2

    def test_deeper_indent_with_no_prior_line_produces_no_block(self):
        lines = [
            "    a = 1",
            "    b = 2",
        ]
        assert scan_indent_regions(lines, _rule()) == []
