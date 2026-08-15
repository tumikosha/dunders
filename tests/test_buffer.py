import dunders.windowing.core.buffer as buffer_mod
from dunders.windowing.core.buffer import TextBuffer


class TestTextBufferCut:
    def test_cut_selection(self, monkeypatch):
        copied = []
        monkeypatch.setattr(buffer_mod, "_copy_to_system", lambda s: copied.append(s))
        buf = TextBuffer.from_string("hello world")
        buf.start_selection(0, 0)
        buf.update_selection(0, 5)  # "hello"
        text = buf.cut_selection()
        assert text == "hello"
        assert copied == ["hello"]
        assert buf.lines == [" world"]
        assert buf.has_selection is False

    def test_cut_no_selection_cuts_line(self, monkeypatch):
        copied = []
        monkeypatch.setattr(buffer_mod, "_copy_to_system", lambda s: copied.append(s))
        buf = TextBuffer.from_string("line1\nline2")
        buf.cursor_row = 0
        text = buf.cut_selection()
        assert text == "line1"
        assert copied == ["line1"]
        assert buf.lines == ["line2"]


class TestTextBufferInsertText:
    def test_insert_text_single_line(self):
        buf = TextBuffer.from_string("ad")
        buf.cursor_col = 1
        buf.insert_text("bc")
        assert buf.lines == ["abcd"]
        assert buf.cursor_col == 3

    def test_insert_text_multiline(self):
        buf = TextBuffer.from_string("ae")
        buf.cursor_col = 1
        buf.insert_text("b\nc\nd")
        assert buf.lines == ["ab", "c", "de"]
        assert buf.cursor_row == 2
        assert buf.cursor_col == 1

    def test_insert_text_empty_is_noop(self):
        buf = TextBuffer.from_string("hello")
        buf.insert_text("")
        assert buf.lines == ["hello"]
        assert buf.modified is False

    def test_insert_text_normalises_cr_newlines(self):
        # Terminal bracketed paste (Cmd+V) sends line breaks as CR, and CRLF
        # text carries a trailing \r — both must split into real lines.
        for payload in ("a\rb\rc", "a\r\nb\r\nc"):
            buf = TextBuffer.from_string("")
            buf.insert_text(payload)
            assert buf.lines == ["a", "b", "c"]
            assert buf.cursor_row == 2


class TestTextBufferInit:
    def test_empty_buffer(self):
        buf = TextBuffer()
        assert buf.lines == [""]
        assert buf.cursor_row == 0
        assert buf.cursor_col == 0

    def test_from_string(self):
        buf = TextBuffer.from_string("hello\nworld")
        assert buf.lines == ["hello", "world"]
        assert buf.cursor_row == 0
        assert buf.cursor_col == 0

    def test_from_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3")
        buf = TextBuffer.from_file(str(f))
        assert buf.lines == ["line1", "line2", "line3"]
        assert buf.file_path == str(f)


class TestTextBufferEditing:
    def test_insert_char(self):
        buf = TextBuffer.from_string("hello")
        buf.insert_char("X")
        assert buf.lines == ["Xhello"]
        assert buf.cursor_col == 1

    def test_insert_char_mid_line(self):
        buf = TextBuffer.from_string("hello")
        buf.cursor_col = 3
        buf.insert_char("X")
        assert buf.lines == ["helXlo"]
        assert buf.cursor_col == 4

    def test_insert_newline(self):
        buf = TextBuffer.from_string("hello")
        buf.cursor_col = 3
        buf.insert_newline()
        assert buf.lines == ["hel", "lo"]
        assert buf.cursor_row == 1
        assert buf.cursor_col == 0

    def test_delete_char_forward(self):
        buf = TextBuffer.from_string("hello")
        buf.cursor_col = 1
        buf.delete_char_forward()
        assert buf.lines == ["hllo"]

    def test_delete_char_backward(self):
        buf = TextBuffer.from_string("hello")
        buf.cursor_col = 3
        buf.delete_char_backward()
        assert buf.lines == ["helo"]
        assert buf.cursor_col == 2

    def test_backspace_at_line_start_joins_lines(self):
        buf = TextBuffer.from_string("hello\nworld")
        buf.cursor_row = 1
        buf.cursor_col = 0
        buf.delete_char_backward()
        assert buf.lines == ["helloworld"]
        assert buf.cursor_row == 0
        assert buf.cursor_col == 5

    def test_delete_forward_at_line_end_joins_lines(self):
        buf = TextBuffer.from_string("hello\nworld")
        buf.cursor_col = 5
        buf.delete_char_forward()
        assert buf.lines == ["helloworld"]


class TestTextBufferCursor:
    def test_move_right(self):
        buf = TextBuffer.from_string("hello")
        buf.move_cursor_right()
        assert buf.cursor_col == 1

    def test_move_right_wraps_to_next_line(self):
        buf = TextBuffer.from_string("ab\ncd")
        buf.cursor_col = 2
        buf.move_cursor_right()
        assert buf.cursor_row == 1
        assert buf.cursor_col == 0

    def test_move_left(self):
        buf = TextBuffer.from_string("hello")
        buf.cursor_col = 3
        buf.move_cursor_left()
        assert buf.cursor_col == 2

    def test_move_left_wraps_to_prev_line(self):
        buf = TextBuffer.from_string("ab\ncd")
        buf.cursor_row = 1
        buf.cursor_col = 0
        buf.move_cursor_left()
        assert buf.cursor_row == 0
        assert buf.cursor_col == 2

    def test_move_up(self):
        buf = TextBuffer.from_string("ab\ncd")
        buf.cursor_row = 1
        buf.move_cursor_up()
        assert buf.cursor_row == 0

    def test_move_down(self):
        buf = TextBuffer.from_string("ab\ncd")
        buf.move_cursor_down()
        assert buf.cursor_row == 1

    def test_move_down_clamps_col(self):
        buf = TextBuffer.from_string("hello\nab")
        buf.cursor_col = 4
        buf.move_cursor_down()
        assert buf.cursor_row == 1
        assert buf.cursor_col == 2


class TestTextBufferSave:
    def test_save(self, tmp_path):
        f = tmp_path / "out.txt"
        buf = TextBuffer.from_string("line1\nline2")
        buf.file_path = str(f)
        buf.save()
        assert f.read_text() == "line1\nline2"
        assert buf.modified is False

    def test_modified_flag(self):
        buf = TextBuffer.from_string("hello")
        assert buf.modified is False
        buf.insert_char("X")
        assert buf.modified is True


class TestTextBufferIndentSelection:
    def test_indents_each_touched_line(self):
        buf = TextBuffer.from_string("a\nb\nc")
        buf.start_selection(0, 1)
        buf.update_selection(1, 1)
        assert buf.indent_selection() is True
        assert buf.lines == ["    a", "    b", "c"]

    def test_a_selection_ending_in_column_zero_leaves_that_line_alone(self):
        """Dragging one line down must not indent the line below it."""
        buf = TextBuffer.from_string("a\nb")
        buf.start_selection(0, 0)
        buf.update_selection(1, 0)
        buf.indent_selection()
        assert buf.lines == ["    a", "b"]

    def test_a_single_line_selection_indents_that_line(self):
        buf = TextBuffer.from_string("hello world")
        buf.start_selection(0, 6)
        buf.update_selection(0, 11)
        buf.indent_selection()
        assert buf.lines == ["    hello world"]
        # The selected word travelled with the indent.
        assert buf.get_selected_text() == "world"

    def test_a_reversed_selection_indents_the_same_lines(self):
        buf = TextBuffer.from_string("a\nb\nc")
        buf.start_selection(2, 1)
        buf.update_selection(1, 1)   # dragged upward
        buf.indent_selection()
        assert buf.lines == ["a", "    b", "    c"]

    def test_column_zero_anchors_stay_put_so_whole_lines_stay_selected(self):
        buf = TextBuffer.from_string("a\nbb")
        buf.start_selection(0, 0)
        buf.update_selection(1, 2)
        buf.indent_selection()
        assert buf.get_selected_text() == "    a\n    bb"

    def test_without_a_selection_it_does_nothing(self):
        buf = TextBuffer.from_string("a")
        assert buf.indent_selection() is False
        assert buf.lines == ["a"]
        assert buf.modified is False

    def test_undo_restores_the_whole_block(self):
        buf = TextBuffer.from_string("a\nb")
        buf.start_selection(0, 0)
        buf.update_selection(1, 1)
        buf.indent_selection()
        buf.undo()
        assert buf.lines == ["a", "b"]


class TestTextBufferUnindentSelection:
    def test_strips_one_stop_off_each_touched_line(self):
        buf = TextBuffer.from_string("        a\n        b\nc")
        buf.start_selection(0, 8)
        buf.update_selection(1, 8)
        assert buf.unindent_selection() is True
        assert buf.lines == ["    a", "    b", "c"]

    def test_a_short_indent_is_removed_whole(self):
        buf = TextBuffer.from_string("  a\n      b")
        buf.start_selection(0, 2)
        buf.update_selection(1, 6)
        buf.unindent_selection()
        assert buf.lines == ["a", "  b"]

    def test_a_leading_tab_counts_as_one_stop(self):
        buf = TextBuffer.from_string("\ta\n\t\tb")
        buf.start_selection(0, 1)
        buf.update_selection(1, 2)
        buf.unindent_selection()
        assert buf.lines == ["a", "\tb"]

    def test_flush_left_lines_do_not_block_the_rest(self):
        buf = TextBuffer.from_string("a\n    b")
        buf.start_selection(0, 0)
        buf.update_selection(1, 5)
        assert buf.unindent_selection() is True
        assert buf.lines == ["a", "b"]

    def test_nothing_to_strip_leaves_the_buffer_untouched(self):
        buf = TextBuffer.from_string("a\nb")
        buf.start_selection(0, 0)
        buf.update_selection(1, 1)
        assert buf.unindent_selection() is False
        assert buf.modified is False

    def test_anchors_follow_their_own_line(self):
        """Lines can lose different amounts, so one shared shift would drift."""
        buf = TextBuffer.from_string("        aaa\n  bbb")
        buf.start_selection(0, 8)   # before "aaa"
        buf.update_selection(1, 5)  # after "bbb"
        buf.unindent_selection()
        assert buf.lines == ["    aaa", "bbb"]
        assert buf.get_selected_text() == "aaa\nbbb"

    def test_undo_restores_the_whole_block(self):
        buf = TextBuffer.from_string("    a\n    b")
        buf.start_selection(0, 0)
        buf.update_selection(1, 5)
        buf.unindent_selection()
        buf.undo()
        assert buf.lines == ["    a", "    b"]

    def test_without_a_selection_it_does_nothing(self):
        buf = TextBuffer.from_string("    a")
        assert buf.unindent_selection() is False
        assert buf.lines == ["    a"]
