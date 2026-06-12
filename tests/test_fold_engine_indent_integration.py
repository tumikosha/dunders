from dunders.windowing.core.fold_engine import FoldEngine, FoldRegistry, FoldRule
from dunders.windowing.core.indent_fold import IndentFoldRule


def _engine(rules):
    reg = FoldRegistry()
    for r in rules:
        reg.add_rule(r)
    return FoldEngine(registry=reg)


class TestFoldEngineWithIndentRule:
    def test_indent_rule_produces_region(self):
        engine = _engine([IndentFoldRule()])
        lines = ["def foo():", "    a = 1", "    b = 2"]
        regions = engine.scan(lines)
        assert len(regions) == 1
        assert regions[0].start_row == 0
        assert regions[0].end_row == 2

    def test_bracket_and_indent_rules_coexist(self):
        engine = _engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
            IndentFoldRule(),
        ])
        lines = [
            "def foo():",
            "    x = {",
            "        1,",
            "        2,",
            "    }",
        ]
        regions = engine.scan(lines)
        kinds = {type(r.rule).__name__ for r in regions}
        assert "FoldRule" in kinds
        assert "IndentFoldRule" in kinds

    def test_block_fold_renders_with_indent_placeholder(self):
        engine = _engine([IndentFoldRule(placeholder=" ⋯")])
        lines = ["def foo():", "    a = 1", "    b = 2"]
        regions = engine.scan(lines)
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions)
        assert rendered == ["def foo(): ⋯"]


class TestShowLineCount:
    def test_block_fold_gets_line_count_suffix(self):
        engine = _engine([IndentFoldRule(placeholder=" ⋯")])
        lines = ["def foo():", "    a = 1", "    b = 2"]
        regions = engine.scan(lines)
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions, show_line_count=True)
        assert rendered == ["def foo(): ⋯  (2 lines)"]

    def test_inline_fold_not_affected_by_line_count(self):
        engine = _engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ['data = {"key": "val"}']
        regions = engine.scan(lines)
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions, show_line_count=True)
        assert rendered == ["data = ⋯"]

    def test_default_is_false_no_suffix(self):
        engine = _engine([IndentFoldRule(placeholder=" ⋯")])
        lines = ["def foo():", "    a = 1", "    b = 2"]
        regions = engine.scan(lines)
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions)
        assert rendered == ["def foo(): ⋯"]
