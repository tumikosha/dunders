from __future__ import annotations

from dataclasses import dataclass, field

# Clipboard access lives in one place (clipboard.py) so the editor, command
# line and panel header all share the same system clipboard. These aliases
# preserve the historical names imported elsewhere (e.g. app.py).
from tyui.windowing.core.clipboard import system_copy as _copy_to_system
from tyui.windowing.core.clipboard import system_paste as _paste_from_system


@dataclass
class TextBuffer:
    lines: list[str] = field(default_factory=lambda: [""])
    cursor_row: int = 0
    cursor_col: int = 0
    file_path: str | None = None
    modified: bool = False
    _clipboard: str = ""
    # Selection: None means no selection
    sel_start_row: int | None = None
    sel_start_col: int | None = None
    sel_end_row: int | None = None
    sel_end_col: int | None = None
    _undo_stack: list = field(default_factory=list)
    _redo_stack: list = field(default_factory=list)
    _max_undo: int = 100

    @classmethod
    def from_string(cls, text: str) -> TextBuffer:
        lines = text.split("\n") if text else [""]
        return cls(lines=lines)

    @classmethod
    def from_file(cls, path: str) -> TextBuffer:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        buf = cls.from_string(text)
        buf.file_path = path
        return buf

    def _save_undo(self) -> None:
        snapshot = (list(self.lines), self.cursor_row, self.cursor_col)
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append((list(self.lines), self.cursor_row, self.cursor_col))
        lines, row, col = self._undo_stack.pop()
        self.lines = lines
        self.cursor_row = row
        self.cursor_col = col
        self.modified = True
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append((list(self.lines), self.cursor_row, self.cursor_col))
        lines, row, col = self._redo_stack.pop()
        self.lines = lines
        self.cursor_row = row
        self.cursor_col = col
        self.modified = True
        return True

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def current_line(self) -> str:
        return self.lines[self.cursor_row]

    def _clamp_cursor(self) -> None:
        self.cursor_row = max(0, min(self.cursor_row, self.line_count - 1))
        self.cursor_col = max(0, min(self.cursor_col, len(self.current_line)))

    def insert_char(self, ch: str) -> None:
        self._save_undo()
        line = self.current_line
        self.lines[self.cursor_row] = line[: self.cursor_col] + ch + line[self.cursor_col :]
        self.cursor_col += len(ch)
        self.modified = True

    def insert_newline(self) -> None:
        self._save_undo()
        line = self.current_line
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        self.lines[self.cursor_row] = before
        self.lines.insert(self.cursor_row + 1, after)
        self.cursor_row += 1
        self.cursor_col = 0
        self.modified = True

    def delete_char_forward(self) -> None:
        self._save_undo()
        line = self.current_line
        if self.cursor_col < len(line):
            self.lines[self.cursor_row] = line[: self.cursor_col] + line[self.cursor_col + 1 :]
            self.modified = True
        elif self.cursor_row < self.line_count - 1:
            next_line = self.lines.pop(self.cursor_row + 1)
            self.lines[self.cursor_row] = line + next_line
            self.modified = True

    def delete_char_backward(self) -> None:
        self._save_undo()
        if self.cursor_col > 0:
            line = self.current_line
            self.lines[self.cursor_row] = line[: self.cursor_col - 1] + line[self.cursor_col :]
            self.cursor_col -= 1
            self.modified = True
        elif self.cursor_row > 0:
            prev_line = self.lines[self.cursor_row - 1]
            current_line = self.lines.pop(self.cursor_row)
            self.cursor_row -= 1
            self.cursor_col = len(prev_line)
            self.lines[self.cursor_row] = prev_line + current_line
            self.modified = True

    def move_cursor_right(self) -> None:
        if self.cursor_col < len(self.current_line):
            self.cursor_col += 1
        elif self.cursor_row < self.line_count - 1:
            self.cursor_row += 1
            self.cursor_col = 0

    def move_cursor_left(self) -> None:
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            self.cursor_row -= 1
            self.cursor_col = len(self.current_line)

    def move_cursor_word_right(self) -> None:
        line = self.current_line
        col = self.cursor_col
        # Skip current word characters
        while col < len(line) and not line[col].isspace():
            col += 1
        # Skip whitespace
        while col < len(line) and line[col].isspace():
            col += 1
        if col >= len(line) and self.cursor_row < self.line_count - 1:
            self.cursor_row += 1
            self.cursor_col = 0
        else:
            self.cursor_col = col

    def move_cursor_word_left(self) -> None:
        col = self.cursor_col
        if col == 0 and self.cursor_row > 0:
            self.cursor_row -= 1
            self.cursor_col = len(self.current_line)
            return
        line = self.current_line
        # Skip whitespace backwards
        while col > 0 and line[col - 1].isspace():
            col -= 1
        # Skip word characters backwards
        while col > 0 and not line[col - 1].isspace():
            col -= 1
        self.cursor_col = col

    def delete_line(self) -> None:
        self._save_undo()
        if self.line_count > 1:
            self.lines.pop(self.cursor_row)
            if self.cursor_row >= self.line_count:
                self.cursor_row = self.line_count - 1
            self._clamp_cursor()
        else:
            self.lines[0] = ""
            self.cursor_col = 0
        self.modified = True

    @property
    def has_selection(self) -> bool:
        return self.sel_start_row is not None

    def start_selection(self, row: int, col: int) -> None:
        self.sel_start_row = row
        self.sel_start_col = col
        self.sel_end_row = row
        self.sel_end_col = col

    def update_selection(self, row: int, col: int) -> None:
        self.sel_end_row = row
        self.sel_end_col = col

    def select_all(self) -> None:
        """Select all text in the buffer."""
        self.sel_start_row = 0
        self.sel_start_col = 0
        self.sel_end_row = self.line_count - 1
        self.sel_end_col = len(self.lines[self.line_count - 1])
        self.cursor_row = self.sel_end_row
        self.cursor_col = self.sel_end_col

    def clear_selection(self) -> None:
        self.sel_start_row = None
        self.sel_start_col = None
        self.sel_end_row = None
        self.sel_end_col = None

    def selection_range(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """Return (start, end) in document order."""
        if not self.has_selection:
            return None
        s = (self.sel_start_row, self.sel_start_col)
        e = (self.sel_end_row, self.sel_end_col)
        if s > e:
            s, e = e, s
        return s, e

    def get_selected_text(self) -> str:
        rng = self.selection_range()
        if not rng:
            return ""
        (sr, sc), (er, ec) = rng
        if sr == er:
            return self.lines[sr][sc:ec]
        parts = [self.lines[sr][sc:]]
        for r in range(sr + 1, er):
            parts.append(self.lines[r])
        parts.append(self.lines[er][:ec])
        return "\n".join(parts)

    def delete_selection(self) -> None:
        self._save_undo()
        rng = self.selection_range()
        if not rng:
            return
        (sr, sc), (er, ec) = rng
        if sr == er:
            line = self.lines[sr]
            self.lines[sr] = line[:sc] + line[ec:]
        else:
            before = self.lines[sr][:sc]
            after = self.lines[er][ec:]
            self.lines[sr] = before + after
            del self.lines[sr + 1 : er + 1]
        self.cursor_row = sr
        self.cursor_col = sc
        self.clear_selection()
        self.modified = True

    def copy_selection(self) -> str:
        text = self.get_selected_text()
        if text:
            self._clipboard = text
        else:
            self._clipboard = self.current_line
        _copy_to_system(self._clipboard)
        return self._clipboard

    def cut_selection(self) -> str:
        """Copy the selection (or current line) to the clipboard, then delete it."""
        text = self.copy_selection()
        if self.has_selection:
            self.delete_selection()
        else:
            self.delete_line()
        return text

    def copy_line(self) -> str:
        self._clipboard = self.current_line
        return self._clipboard

    def paste(self, fallback: str = "") -> None:
        system_text = _paste_from_system()
        if system_text:
            self._clipboard = system_text
        elif fallback:
            # OSC 52 / Textual clipboard fallback (SSH where pbpaste is empty).
            self._clipboard = fallback
        self.insert_text(self._clipboard)

    def insert_text(self, text: str) -> None:
        """Insert arbitrary (possibly multi-line) text at the cursor.

        Shared by clipboard paste and terminal bracketed-paste (Cmd+V).
        """
        if not text:
            return
        self._save_undo()
        paste_lines = text.split("\n")
        if len(paste_lines) == 1:
            self.insert_char(paste_lines[0])
        else:
            # Insert first part into current line
            line = self.current_line
            before = line[:self.cursor_col]
            after = line[self.cursor_col:]
            self.lines[self.cursor_row] = before + paste_lines[0]
            # Insert middle lines
            for i, pl in enumerate(paste_lines[1:-1], 1):
                self.lines.insert(self.cursor_row + i, pl)
            # Insert last part + remainder
            last_line = paste_lines[-1]
            self.lines.insert(self.cursor_row + len(paste_lines) - 1, last_line + after)
            self.cursor_row += len(paste_lines) - 1
            self.cursor_col = len(last_line)
            self.modified = True

    def duplicate_line(self) -> None:
        self._save_undo()
        line = self.current_line
        self.lines.insert(self.cursor_row + 1, line)
        self.cursor_row += 1
        self.modified = True

    def delete_word_backward(self) -> None:
        self._save_undo()
        if self.cursor_col == 0:
            if self.cursor_row > 0:
                self.delete_char_backward()
            return
        line = self.current_line
        col = self.cursor_col
        # Skip whitespace backwards
        while col > 0 and line[col - 1].isspace():
            col -= 1
        # Skip word characters backwards
        while col > 0 and not line[col - 1].isspace():
            col -= 1
        self.lines[self.cursor_row] = line[:col] + line[self.cursor_col:]
        self.cursor_col = col
        self.modified = True

    def move_cursor_up(self) -> None:
        if self.cursor_row > 0:
            self.cursor_row -= 1
            self._clamp_cursor()

    def move_cursor_down(self) -> None:
        if self.cursor_row < self.line_count - 1:
            self.cursor_row += 1
            self._clamp_cursor()

    def move_cursor_document_start(self) -> None:
        self.cursor_row = 0
        self.cursor_col = 0

    def move_cursor_document_end(self) -> None:
        self.cursor_row = self.line_count - 1
        self.cursor_col = len(self.lines[self.cursor_row])

    def find_all(self, query: str, case_sensitive: bool = True) -> list[tuple[int, int, int]]:
        """Return list of (row, col, length) for all matches."""
        if not query:
            return []
        results = []
        search = query if case_sensitive else query.lower()
        for row_idx, line in enumerate(self.lines):
            hay = line if case_sensitive else line.lower()
            start = 0
            while True:
                pos = hay.find(search, start)
                if pos == -1:
                    break
                results.append((row_idx, pos, len(query)))
                start = pos + 1
        return results

    def save(self) -> None:
        if self.file_path is None:
            raise ValueError("No file path set")
        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines))
        self.modified = False
