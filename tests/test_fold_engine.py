from tyui.windowing.core.fold_engine import FoldEngine, FoldRegistry, FoldRegion, FoldRule


class TestFoldRule:
    def test_create_rule(self):
        rule = FoldRule(start_label="{", end_label="}", placeholder="⋯")
        assert rule.start_label == "{"
        assert rule.end_label == "}"
        assert rule.placeholder == "⋯"
        assert rule.nested is True
        assert rule.priority == 0


class TestFoldRegistry:
    def test_add_and_get_rules(self):
        reg = FoldRegistry()
        rule = FoldRule(start_label="{", end_label="}", placeholder="⋯")
        reg.add_rule(rule)
        assert rule in reg.rules

    def test_rules_sorted_by_priority(self):
        reg = FoldRegistry()
        low = FoldRule(start_label="(", end_label=")", placeholder="…", priority=0)
        high = FoldRule(start_label="{", end_label="}", placeholder="⋯", priority=10)
        reg.add_rule(low)
        reg.add_rule(high)
        assert reg.rules[0] is high
        assert reg.rules[1] is low

    def test_remove_rule(self):
        reg = FoldRegistry()
        rule = FoldRule(start_label="{", end_label="}", placeholder="⋯")
        reg.add_rule(rule)
        reg.remove_rule(rule)
        assert rule not in reg.rules


class TestFoldEngine:
    def _make_engine(self, rules: list[FoldRule] | None = None) -> FoldEngine:
        reg = FoldRegistry()
        for r in (rules or []):
            reg.add_rule(r)
        return FoldEngine(registry=reg)

    def test_scan_inline_fold(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ['config = {"host": "localhost", "port": 8080}']
        regions = engine.scan(lines)
        assert len(regions) == 1
        r = regions[0]
        assert r.start_row == 0
        assert r.start_col == 9
        assert r.end_row == 0
        assert r.end_col == 43
        assert r.rule.placeholder == "⋯"

    def test_scan_block_fold(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ["func() {", "  body", "}"]
        regions = engine.scan(lines)
        assert len(regions) == 1
        r = regions[0]
        assert r.start_row == 0
        assert r.end_row == 2

    def test_scan_nested_folds(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ['{ { inner } }']
        regions = engine.scan(lines)
        assert len(regions) == 2
        inner = min(regions, key=lambda r: r.end_col - r.start_col)
        assert "inner" in lines[0][inner.start_col:inner.end_col + 1]

    def test_scan_no_match(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ["no braces here"]
        regions = engine.scan(lines)
        assert regions == []

    def test_toggle_fold(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ['data = {"key": "val"}']
        regions = engine.scan(lines)
        assert regions[0].collapsed is False
        engine.toggle_fold(regions[0])
        assert regions[0].collapsed is True
        engine.toggle_fold(regions[0])
        assert regions[0].collapsed is False

    def test_render_with_inline_fold_collapsed(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ['data = {"key": "val"}']
        regions = engine.scan(lines)
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions)
        assert rendered == ["data = ⋯"]

    def test_render_with_block_fold_collapsed(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ["func() {", "  body", "}"]
        regions = engine.scan(lines)
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions)
        assert rendered == ["func() ⋯"]

    def test_render_uncollapsed_returns_original(self):
        engine = self._make_engine([
            FoldRule(start_label="{", end_label="}", placeholder="⋯"),
        ])
        lines = ["func() {", "  body", "}"]
        regions = engine.scan(lines)
        rendered = engine.render_lines(lines, regions)
        assert rendered == lines

    def test_multi_label_fold(self):
        engine = self._make_engine([
            FoldRule(start_label="#region", end_label="#endregion", placeholder="▸ region"),
        ])
        lines = ["#region Setup", "a = 1", "b = 2", "#endregion"]
        regions = engine.scan(lines)
        assert len(regions) == 1
        engine.toggle_fold(regions[0])
        rendered = engine.render_lines(lines, regions)
        assert rendered == ["▸ region"]
