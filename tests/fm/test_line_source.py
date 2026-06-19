from dunders.fm.line_source import LineSource, TextSource, MmapSource, PREFIX_INDEX_LINES


class TestTextSource:
    def test_line_access_and_count(self):
        s = TextSource("a\nb\nc")
        assert s.line_count() == 3
        assert s.line(0) == "a"
        assert s.line(2) == "c"
        assert s.is_complete() is True

    def test_empty_yields_one_blank_line(self):
        s = TextSource("")
        assert s.line_count() == 1
        assert s.line(0) == ""

    def test_out_of_range_is_blank(self):
        s = TextSource("only")
        assert s.line(5) == ""


class TestMmapSource:
    def test_lazy_index_and_lines(self, tmp_path):
        p = tmp_path / "big.txt"
        p.write_text("".join(f"line {i}\n" for i in range(5000)))
        src = MmapSource(p)
        # Opening indexes only a prefix; the full count is not known yet.
        assert src.line_count() <= PREFIX_INDEX_LINES + 1
        assert src.line(4999) == "line 4999"          # pulls index forward
        while src.index_batch(4096):
            pass
        assert src.is_complete() is True
        assert src.line_count() == 5000
        src.close()

    def test_subclass_of_linesource(self):
        assert issubclass(TextSource, LineSource)
        assert issubclass(MmapSource, LineSource)
