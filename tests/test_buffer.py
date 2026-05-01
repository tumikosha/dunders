from tyui.windowing.core.buffer import TextBuffer


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
