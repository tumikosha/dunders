from dunders.fm.csv_viewer import (
    CsvCellDialog,
    CsvViewerContent,
    CsvViewerWidget,
    column_widths,
    decode_text,
    fit_cell,
    parse_csv,
    sniff_delimiter,
)


class TestDecodeText:
    def test_plain_utf8(self):
        assert decode_text(b"a,b\n1,2\n") == "a,b\n1,2\n"

    def test_utf8_bom_stripped(self):
        assert decode_text("﻿a,b\n".encode("utf-8")) == "a,b\n"

    def test_utf16_le_bom(self):
        assert decode_text("naïve,café\n".encode("utf-16")) == "naïve,café\n"

    def test_utf16_no_bom_guessed_from_nuls(self):
        raw = "a,b\n1,2\n".encode("utf-16-le")  # no BOM, NUL-heavy
        assert decode_text(raw) == "a,b\n1,2\n"


class TestSniffDelimiter:
    def test_comma(self):
        assert sniff_delimiter("a,b,c\n1,2,3\n") == ","

    def test_semicolon(self):
        assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"

    def test_tab(self):
        assert sniff_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"

    def test_pipe(self):
        assert sniff_delimiter("a|b|c\n1|2|3\n") == "|"

    def test_empty_falls_back_to_comma(self):
        assert sniff_delimiter("") == ","

    def test_single_column_falls_back_to_comma(self):
        assert sniff_delimiter("justonecolumn\nanotherline\n") == ","


class TestParseCsv:
    def test_simple(self):
        assert parse_csv("a,b\n1,2\n", ",") == [["a", "b"], ["1", "2"]]

    def test_quoted_field_with_embedded_delimiter(self):
        rows = parse_csv('name,note\n"Smith, Jr.",hi\n', ",")
        assert rows == [["name", "note"], ["Smith, Jr.", "hi"]]

    def test_quoted_field_with_embedded_newline(self):
        rows = parse_csv('a,b\n"line1\nline2",x\n', ",")
        assert rows == [["a", "b"], ["line1\nline2", "x"]]

    def test_tab_delimited(self):
        assert parse_csv("a\tb\n1\t2\n", "\t") == [["a", "b"], ["1", "2"]]


class TestColumnWidths:
    def test_widest_cell_per_column(self):
        rows = [["a", "bb"], ["ccc", "d"]]
        assert column_widths(rows) == [3, 2]

    def test_ragged_rows_extend_columns(self):
        rows = [["a"], ["b", "cc", "ddd"]]
        assert column_widths(rows) == [1, 2, 3]

    def test_caps_at_max_width(self):
        rows = [["x" * 100]]
        assert column_widths(rows, max_width=10) == [10]

    def test_cjk_counts_double_width(self):
        # Each full-width glyph spans 2 terminal cells: "onebox" (6) + 4×2 = 14.
        rows = [["onebox株式会社"], ["ab"]]
        assert column_widths(rows) == [14]


class TestFitCell:
    def test_pads_short(self):
        assert fit_cell("ab", 5) == "ab   "

    def test_truncates_with_ellipsis(self):
        assert fit_cell("abcdef", 4) == "abc…"

    def test_exact_fit(self):
        assert fit_cell("abcd", 4) == "abcd"

    def test_newlines_and_tabs_flattened(self):
        assert fit_cell("a\nb", 3) == "a b"

    def test_cjk_padded_to_cell_width(self):
        from rich.cells import cell_len

        # Padding lands on real terminal columns, not character counts.
        assert cell_len(fit_cell("株式", 6)) == 6
        # Truncation keeps the result within the target cell width.
        assert cell_len(fit_cell("株式会社株式会社", 5)) == 5

    def test_orphan_combining_marks_stripped(self):
        import unicodedata
        from rich.cells import cell_len

        # Turkish dotted-i: base "i" + U+0307 (combining dot above). Terminals
        # can't compose the orphan mark, so it must be dropped or columns shift.
        cell = fit_cell("ti̇caret", 7)
        assert not any(unicodedata.combining(ch) for ch in cell)
        assert cell == "ticaret"
        assert cell_len(cell) == 7

    def test_composable_diacritics_preserved(self):
        # NFC keeps Latin/European text intact (only un-composable marks go).
        assert fit_cell("Việt", 4).rstrip() == "Việt"


class TestCsvViewerContent:
    async def test_opens_in_table_mode_and_toggles(self, tmp_path):
        from textual.app import App

        class _Host(App):
            def compose(self):
                yield CsvViewerContent("a,b,c\n1,2,3\n", display_name="data.csv")

        app = _Host()
        async with app.run_test():
            content = app.query_one(CsvViewerContent)
            assert content.widget.mode == "table"
            assert content.widget.delimiter == ","
            assert content.widget.n_cols == 3
            assert content.widget.n_rows == 2
            assert "TABLE" in content.window_subtitle
            content._toggle_mode()
            assert content.widget.mode == "raw"
            assert "RAW" in content.window_subtitle

    async def test_mode_toolbar_button_toggles_and_relabels(self, tmp_path):
        from textual.app import App

        class _Host(App):
            def compose(self):
                yield CsvViewerContent("a,b,c\n1,2,3\n", display_name="data.csv")

        app = _Host()
        async with app.run_test():
            content = app.query_one(CsvViewerContent)
            # Button shows the mode it switches TO: "[ Raw ]" while in table.
            assert content._mode_btn.label.plain == "[ Raw ]"
            content._mode_btn._on_press()  # same path as a click
            assert content.widget.mode == "raw"
            assert content._mode_btn.label.plain == "[ Table ]"
            content._mode_btn._on_press()
            assert content.widget.mode == "table"
            assert content._mode_btn.label.plain == "[ Raw ]"

    async def test_cycle_delimiter_reparses(self, tmp_path):
        from textual.app import App

        # Sniffs as comma; one column. Cycling to ';' splits into 3 columns.
        class _Host(App):
            def compose(self):
                yield CsvViewerContent("a;b;c\n1;2;3\n", display_name="x.csv")

        app = _Host()
        async with app.run_test():
            content = app.query_one(CsvViewerContent)
            # Auto-detect already picks ';' here.
            assert content.widget.delimiter == ";"
            start = content.widget.delimiter
            content._cycle_delimiter()
            assert content.widget.delimiter != start

    def test_from_bytes(self):
        content = CsvViewerContent.from_bytes("r.csv", b"a,b\n1,2\n")
        assert content.window_title == "CSV: r.csv"
        assert content.widget.n_cols == 2

    async def test_horizontal_scroll_shifts_render(self, tmp_path):
        """A table wider than the viewport must shift when scrolled right —
        render_line has to apply scroll_offset.x, not just .y."""
        from textual.app import App

        header = ",".join(f"column_{i:02d}" for i in range(20))
        row = ",".join(f"val{i:02d}" for i in range(20))

        class _Host(App):
            def compose(self):
                yield CsvViewerContent(f"{header}\n{row}\n", display_name="w.csv")

        app = _Host()
        async with app.run_test() as pilot:
            content = app.query_one(CsvViewerContent)
            w = content.widget
            assert w.virtual_size.width > w.size.width  # actually scrollable
            at0 = "".join(s.text for s in w.render_line(0))
            w.scroll_to(30, 0, animate=False)
            await pilot.pause()
            assert w.scroll_offset.x == 30
            at30 = "".join(s.text for s in w.render_line(0))
            assert at0 != at30
            assert "column_00" in at0
            assert "column_00" not in at30  # the first column scrolled off


class TestGutterAndHeader:
    async def _mount(self, text):
        from textual.app import App

        content = CsvViewerContent(text, display_name="d.csv")

        class _Host(App):
            def compose(self):
                yield content

        return _Host(), content

    async def test_line_number_gutter(self):
        app, content = await self._mount(
            "name,age\n" + "".join(f"p{i},{i}\n" for i in range(30))
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            w = content.widget
            # Header row: blank gutter (it's not a numbered data row).
            assert "".join(s.text for s in w.render_line(0)).startswith("    ")
            # First data row carries its number, right-justified in the gutter.
            first = "".join(s.text for s in w.render_line(1))
            assert first.lstrip().startswith("1 ")
            assert "p0" in first
            # Gutter is styled with the line-number role (a distinct colour).
            gutter_seg = next(iter(w.render_line(1)))
            assert "1" in gutter_seg.text
            assert gutter_seg.style is not None

    async def test_header_frozen_on_vertical_scroll(self):
        app, content = await self._mount(
            "name,age,city\n" + "".join(f"p{i},{i},c{i}\n" for i in range(100))
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            w = content.widget
            w.scroll_to(0, 40, animate=False)
            await pilot.pause()
            assert w.scroll_offset.y == 40
            top = "".join(s.text for s in w.render_line(0))
            assert "name" in top and "age" in top and "city" in top
            # The row just below the frozen header is a scrolled data row
            # (idx = scroll_y + 1 = 41 → "p40"), numbered 41 in the gutter.
            below = "".join(s.text for s in w.render_line(1))
            assert "p40" in below
            assert below.lstrip().startswith("41 ")


class TestFilter:
    async def _mount(self, text):
        from textual.app import App

        content = CsvViewerContent(text, display_name="d.csv")

        class _Host(App):
            def compose(self):
                yield content

        return _Host(), content

    async def test_filter_shows_only_matching_rows(self):
        app, content = await self._mount(
            "name,city\nAlice,Paris\nBob,London\nCarol,Paris\nDave,Berlin\n"
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            w = content.widget
            content.apply_filter("Paris")
            await pilot.pause()
            assert w.match_count == 2
            # Header frozen + the two matching rows = 3 virtual rows.
            assert w.virtual_size.height == 3
            header = "".join(s.text for s in w.render_line(0))
            r1 = "".join(s.text for s in w.render_line(1))
            r2 = "".join(s.text for s in w.render_line(2))
            assert "name" in header and "city" in header
            assert "Alice" in r1 and "Carol" in r2
            assert "Bob" not in (r1 + r2) and "Dave" not in (r1 + r2)
            # Gutter keeps the ORIGINAL line numbers (rows 1 and 3).
            assert r1.lstrip().startswith("1 ")
            assert r2.lstrip().startswith("3 ")

    async def test_filter_is_case_insensitive(self):
        app, content = await self._mount("h\nApple\nbanana\nAPRICOT\n")
        async with app.run_test() as pilot:
            await pilot.pause()
            content.apply_filter("ap")
            await pilot.pause()
            # Apple + APRICOT match "ap" case-insensitively; banana does not.
            assert content.widget.match_count == 2

    async def test_empty_filter_clears(self):
        app, content = await self._mount("h\na\nb\nc\n")
        async with app.run_test() as pilot:
            await pilot.pause()
            content.apply_filter("a")
            await pilot.pause()
            assert content.widget.match_count == 1
            content.apply_filter("   ")  # blank clears
            await pilot.pause()
            assert content.widget.match_count is None
            assert content.widget.filter_query is None

    async def test_filter_no_matches_shows_only_header(self):
        app, content = await self._mount("name,age\nAlice,30\nBob,25\n")
        async with app.run_test() as pilot:
            await pilot.pause()
            content.apply_filter("zzz")
            await pilot.pause()
            assert content.widget.match_count == 0
            assert content.widget.virtual_size.height == 1  # header only
            header = "".join(s.text for s in content.widget.render_line(0))
            assert "name" in header

    def test_filter_command_bound_to_ctrl_f(self):
        content = CsvViewerContent("a,b\n1,2\n", display_name="d.csv")
        cmd = next(c for c in content.get_commands() if c.id == "csv.filter")
        assert cmd.hotkey == "ctrl+f"

    async def test_filter_dialog_keeps_focus_on_viewer(self, tmp_path):
        """After submitting the filter, focus must return to the CSV viewer —
        not get stranded on a file panel by the modal-close focus restore."""
        from textual.widgets import Input

        from dunders.app import DundersApp

        f = tmp_path / "data.csv"
        f.write_text("name,city\nAlice,Paris\nBob,London\nCarol,Paris\n")
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            content = next(
                w.content for w in app.desktop.windows
                if isinstance(w.content, CsvViewerContent)
            )
            content._request_filter()
            await pilot.pause()
            await pilot.pause()
            inp = app.query_one(Input)
            inp.focus()
            await pilot.pause()
            inp.value = "Paris"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert content.widget.match_count == 2
            assert isinstance(app.focused, CsvViewerWidget)


class TestCellCursor:
    async def _mount(self, text, size=(40, 12)):
        from textual.app import App

        content = CsvViewerContent(text, display_name="d.csv")

        class _Host(App):
            def compose(self):
                yield content

        return _Host(), content, size

    async def test_arrows_move_cursor_and_clamp(self):
        text = "name,age,city\n" + "".join(f"p{i},{i},c{i}\n" for i in range(60))
        app, content, size = await self._mount(text)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            assert w._cursor_disp == 0 and w._cursor_col == 0
            w.action_scroll_lines(1)
            assert w._cursor_disp == 1
            w.action_scroll_cols(1)
            assert w._cursor_col == 1
            # Clamp low.
            w.action_scroll_lines(-9)
            assert w._cursor_disp == 0
            w.action_scroll_cols(-9)
            assert w._cursor_col == 0
            # Clamp high (3 columns → max col index 2).
            for _ in range(10):
                w.action_scroll_cols(1)
            assert w._cursor_col == 2
            for _ in range(200):
                w.action_scroll_lines(1)
            assert w._cursor_disp == w._body_count() - 1
            # home/end set the column to first/last.
            w.action_scroll_home()
            assert w._cursor_col == 0
            w.action_scroll_end()
            assert w._cursor_col == 2

    async def test_scroll_to_cursor_keeps_far_cell_visible(self):
        text = "name,age,city\n" + "".join(f"p{i},{i},c{i}\n" for i in range(80))
        app, content, size = await self._mount(text, size=(40, 12))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w.scroll_to(0, 0, animate=False)
            await pilot.pause()
            for _ in range(60):
                w.action_scroll_lines(1)
            await pilot.pause()
            sy = int(w.scroll_offset.y)
            # Cursor renders at y = 1 + (disp - scroll_y); must be on-screen.
            ry = 1 + (w._cursor_disp - sy)
            assert 1 <= ry <= w.size.height - 1

    async def test_scroll_to_cursor_keeps_far_right_cell_visible(self):
        header = ",".join(f"column_{i:02d}" for i in range(20))
        row = ",".join(f"val{i:02d}" for i in range(20))
        app, content, size = await self._mount(f"{header}\n{row}\n", size=(30, 10))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w._cursor_disp = 0
            w.action_scroll_end()  # jump to the last column
            await pilot.pause()
            col = w._cursor_col
            x0 = sum(w._widths[:col]) + 3 * col
            x1 = x0 + w._widths[col]
            sx = int(w.scroll_offset.x)
            body_w = w.size.width - w._gutter_width()
            assert x0 >= sx and x1 <= sx + body_w

    async def test_cursor_stays_above_horizontal_scrollbar(self):
        # A WIDE + tall table shows a horizontal scrollbar, which steals the
        # bottom row. The cursor must stay within the scrollable content region
        # (not land one line below, hidden under the scrollbar).
        header = ",".join(f"col{i:02d}" for i in range(20))
        rows = "".join(
            ",".join(f"v{i}{r}" for i in range(20)) + "\n" for r in range(60)
        )
        app, content, size = await self._mount(f"{header}\n{rows}", size=(40, 12))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w.scroll_to(0, 0, animate=False)
            await pilot.pause()
            for _ in range(55):
                w.action_scroll_lines(1)
            await pilot.pause()
            ch = w.scrollable_content_region.height
            assert ch < w.size.height  # the h-scrollbar really stole a row
            ry = 1 + (w._cursor_disp - int(w.scroll_offset.y))
            assert 1 <= ry <= ch - 1  # within content, above the scrollbar

    async def test_cursor_row_renders_highlighted_cell(self):
        text = "name,age,city\n" + "".join(f"p{i},{i},c{i}\n" for i in range(20))
        app, content, size = await self._mount(text)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w._cursor_disp = 2
            w._cursor_col = 1
            w.scroll_to(0, 0, animate=False)
            w.refresh()
            await pilot.pause()
            # disp=2 renders at y = 1 + (2 - 0) = 3. The cursor cell is drawn
            # reverse-video (theme-independent), so a reversed segment must exist.
            strip = w.render_line(3)
            styles = [seg.style for seg in strip if seg.style is not None]
            assert any(getattr(s, "reverse", False) for s in styles)

    async def test_enter_opens_dialog_with_full_value(self, tmp_path):
        from dunders.app import DundersApp

        long_val = "x" * 100  # longer than the table's _MAX_COL_WIDTH cap (48)
        f = tmp_path / "d.csv"
        f.write_text(f"name,note\nAlice,{long_val}\nBob,short\n")
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            content = next(
                w.content for w in app.desktop.windows
                if isinstance(w.content, CsvViewerContent)
            )
            w = content.widget
            w._cursor_disp = 0  # source line 1 (Alice)
            w._cursor_col = 1   # the note column
            assert w._cursor_cell_value() == long_val
            w.action_activate()
            await pilot.pause()
            await pilot.pause()
            dialogs = app.query(CsvCellDialog)
            assert len(dialogs) == 1
            area = dialogs.first()._area
            assert area.text == long_val          # un-truncated
            assert area.read_only is True

    async def test_esc_returns_focus_to_widget_for_cursor_nav(self, tmp_path):
        from dunders.app import DundersApp

        f = tmp_path / "d.csv"
        f.write_text("a,b\n1,2\n3,4\n")
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test(size=(60, 12)) as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            content = next(
                w.content for w in app.desktop.windows
                if isinstance(w.content, CsvViewerContent)
            )
            w = content.widget
            await pilot.press("enter")          # open the cell dialog
            await pilot.pause()
            await pilot.pause()
            assert len(app.query(CsvCellDialog)) == 1
            await pilot.press("escape")         # close it
            await pilot.pause()
            await pilot.pause()
            assert not app.query(CsvCellDialog)
            assert w.has_focus                  # focus returned to the table
            before = (w._cursor_disp, w._cursor_col)
            await pilot.press("down")           # cursor navigation resumes
            assert (w._cursor_disp, w._cursor_col) != before

    async def test_click_opens_dialog_on_cell(self, tmp_path):
        from textual import events

        from dunders.app import DundersApp

        f = tmp_path / "d.csv"
        f.write_text("name,age,city\n" + "".join(f"p{i},{i},c{i}\n" for i in range(20)))
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            content = next(
                w.content for w in app.desktop.windows
                if isinstance(w.content, CsvViewerContent)
            )
            w = content.widget
            w.scroll_to(0, 0, animate=False)
            await pilot.pause()
            gutter = w._gutter_width()
            # Click on y=2 (display row 1 → source line 2 → "p1"), first column.
            ev = events.Click(
                w, x=gutter + 1, y=2, delta_x=0, delta_y=0, button=1,
                shift=False, meta=False, ctrl=False,
            )
            w.on_click(ev)
            await pilot.pause()
            await pilot.pause()
            assert w._cursor_disp == 1 and w._cursor_col == 0
            dialogs = app.query(CsvCellDialog)
            assert len(dialogs) == 1
            assert dialogs.first()._area.text == "p1"

    async def test_click_on_gutter_is_guarded(self):
        from textual import events

        # A click inside the line-number gutter is out of range: the cursor must
        # not move (the guard returns before any cell mapping).
        text = "name,age\n" + "".join(f"p{i},{i}\n" for i in range(10))
        app, content, size = await self._mount(text)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w._cursor_disp = 0
            w._cursor_col = 0
            ev = events.Click(
                w, x=0, y=2, delta_x=0, delta_y=0, button=1,
                shift=False, meta=False, ctrl=False,
            )
            w.on_click(ev)
            await pilot.pause()
            assert w._cursor_disp == 0 and w._cursor_col == 0

    async def test_cell_dialog_preserves_newlines(self):
        # The read-only TextArea keeps embedded newlines verbatim.
        dialog = CsvCellDialog("line1\nline2\nline3")
        assert dialog._area.text == "line1\nline2\nline3"
        assert dialog._area.read_only is True

    async def test_cell_dialog_unescapes_literal_newlines(self):
        # A cell parsed from one physical line carries the literal escape "\n"
        # (backslash + n); the dialog renders those as real line breaks.
        dialog = CsvCellDialog("a\\nb\\r\\nc")
        assert dialog._area.text == "a\nb\nc"

    async def test_cell_dialog_painted_from_palette(self, tmp_path):
        # Per docs/textual-ui-cookbook.md the dialog must paint its surface from
        # the palette (not keep Textual's stock $surface), and survive a theme
        # switch.
        from dunders.app import DundersApp
        from dunders.windowing.helpers import show_modal

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            dialog = CsvCellDialog("hello")
            show_modal(app.desktop, dialog, title="Cell", size=(60, 12))
            await pilot.pause()
            await pilot.pause()
            assert dialog.styles.background is not None       # surface painted
            assert dialog._area.styles.background is not None  # text area painted
            assert dialog._area.highlight_cursor_line is False
            app.action_cycle_theme()
            dialog.apply_theme()
            await pilot.pause()
            assert app.query_one(CsvCellDialog) is dialog      # survived

    async def test_horizontal_wheel_does_not_scroll_x(self):
        from textual import events

        header = ",".join(f"column_{i:02d}" for i in range(20))
        row = ",".join(f"val{i:02d}" for i in range(20))
        app, content, size = await self._mount(f"{header}\n{row}\n", size=(30, 10))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w.scroll_to(10, 0, animate=False)
            await pilot.pause()
            x_before = int(w.scroll_offset.x)
            ev = events.MouseScrollLeft(
                w, x=5, y=5, delta_x=-1, delta_y=0, button=0,
                shift=False, meta=False, ctrl=False,
            )
            w._on_mouse_scroll_left(ev)
            await pilot.pause()
            assert int(w.scroll_offset.x) == x_before
            ev2 = events.MouseScrollRight(
                w, x=5, y=5, delta_x=1, delta_y=0, button=0,
                shift=False, meta=False, ctrl=False,
            )
            w._on_mouse_scroll_right(ev2)
            await pilot.pause()
            assert int(w.scroll_offset.x) == x_before

    async def test_vertical_scroll_still_works(self):
        text = "name,age\n" + "".join(f"p{i},{i}\n" for i in range(60))
        app, content, size = await self._mount(text)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            w.scroll_to(0, 0, animate=False)
            await pilot.pause()
            w.scroll_to(0, 5, animate=False)
            await pilot.pause()
            assert int(w.scroll_offset.y) == 5

    async def test_raw_mode_arrows_still_scroll(self):
        text = "name,age\n" + "".join(f"p{i},{i}\n" for i in range(60))
        app, content, size = await self._mount(text)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            w = content.widget
            content._toggle_mode()
            assert w.mode == "raw"
            w.scroll_to(0, 0, animate=False)
            await pilot.pause()
            y0 = int(w.scroll_offset.y)
            w.action_scroll_lines(3)
            await pilot.pause()
            assert int(w.scroll_offset.y) == y0 + 3
            # Raw mode never opens the cell dialog.
            w.action_activate()
            await pilot.pause()
            assert len(app.query(CsvCellDialog)) == 0


class TestRemoteMember:
    async def test_large_csv_member_streams_to_temp_and_opens(self, tmp_path):
        """A >4 MiB CSV inside an archive (no local path to mmap) is streamed to
        a temp file and opened lazily, instead of being refused as too large."""
        import zipfile

        from dunders.app import DundersApp
        from dunders.core.vfs import VfsPath
        from dunders.fm.providers.zip_provider import ZipProvider

        payload = ("a,b,c\n" * (1024 * 1024)).encode()  # ~6 MiB uncompressed
        zpath = tmp_path / "arc.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.csv", payload)
        root = VfsPath(scheme="zip", root=str(zpath), parts=())
        entry = next(
            e for e in ZipProvider().scan(root, include_parent=False)
            if e.name == "data.csv"
        )
        assert entry.size > DundersApp._HEX_VIEW_SIZE_THRESHOLD

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_member_view(entry)
            csv_win = None
            for _ in range(40):
                await pilot.pause()
                csv_win = next(
                    (w for w in app.desktop.windows
                     if isinstance(w.content, CsvViewerContent)),
                    None,
                )
                if csv_win is not None:
                    break
            assert csv_win is not None, "CSV viewer never opened"
            content = csv_win.content
            assert content.widget.n_cols == 3
            # POSIX: the temp is unlinked right after mmap, so it can't leak even
            # on a crash; the data is still readable through the open mapping.
            assert content._cleanup_path is None
            line = "".join(s.text for s in content.widget.render_line(1))
            assert "a" in line and "b" in line
            app.desktop.remove_window(csv_win)
            await pilot.pause()

    async def test_scratch_sweep_removes_orphans(self, tmp_path):
        """Member temps orphaned by a crash are swept on the next launch."""
        import tempfile
        from pathlib import Path

        from dunders.app import DundersApp

        scratch = Path(tempfile.gettempdir()) / "dunders"
        scratch.mkdir(parents=True, exist_ok=True)
        orphan = scratch / "member-orphan-test.csv"
        orphan.write_text("x")
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not orphan.exists()  # swept by on_mount

    async def test_member_above_cap_is_refused(self, tmp_path, monkeypatch):
        """Beyond the remote cap the member is refused (not downloaded)."""
        import zipfile

        from dunders.app import DundersApp
        from dunders.core.vfs import VfsPath
        from dunders.fm.providers.zip_provider import ZipProvider

        monkeypatch.setattr(DundersApp, "_CSV_REMOTE_SIZE_THRESHOLD", 1024)
        zpath = tmp_path / "arc.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.csv", ("a,b,c\n" * 1_000_000).encode())  # ~6 MiB
        root = VfsPath(scheme="zip", root=str(zpath), parts=())
        entry = next(
            e for e in ZipProvider().scan(root, include_parent=False)
            if e.name == "data.csv"
        )
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_member_view(entry)
            for _ in range(10):
                await pilot.pause()
            assert not any(
                isinstance(w.content, CsvViewerContent) for w in app.desktop.windows
            )


class TestRouting:
    async def test_f3_on_csv_opens_csv_viewer(self, tmp_path):
        from dunders.app import DundersApp

        f = tmp_path / "data.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n")
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            assert any(
                isinstance(w.content, CsvViewerContent) for w in app.desktop.windows
            )

    async def test_f3_on_plain_text_does_not_use_csv_viewer(self, tmp_path):
        from dunders.app import DundersApp
        from dunders.fm.viewer import ViewerContent

        f = tmp_path / "notes.txt"
        f.write_text("hello,world\n")  # comma content but not a .csv name
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            windows = list(app.desktop.windows)
            assert not any(isinstance(w.content, CsvViewerContent) for w in windows)
            assert any(isinstance(w.content, ViewerContent) for w in windows)

    def test_looks_csv_matches_extensions(self):
        from dunders.app import DundersApp

        assert DundersApp._looks_csv("x.csv") is True
        assert DundersApp._looks_csv("X.CSV") is True
        assert DundersApp._looks_csv("data.tsv") is True
        assert DundersApp._looks_csv("notes.txt") is False

    async def test_f3_on_utf16_csv_opens_csv_not_hex(self, tmp_path):
        """Excel exports CSV as UTF-16 (NUL-heavy) — it used to sniff as binary
        and open in the hex viewer."""
        from dunders.app import DundersApp
        from dunders.fm.hex_viewer import HexViewerContent

        f = tmp_path / "excel.csv"
        f.write_bytes("name,age\nAlice,30\nBob,25\n".encode("utf-16"))
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            windows = list(app.desktop.windows)
            assert any(isinstance(w.content, CsvViewerContent) for w in windows)
            assert not any(isinstance(w.content, HexViewerContent) for w in windows)

    async def test_f3_on_large_csv_opens_csv_not_hex(self, tmp_path):
        """A CSV bigger than the 4 MiB hex threshold should still tabulate."""
        from dunders.app import DundersApp
        from dunders.fm.hex_viewer import HexViewerContent

        f = tmp_path / "big.csv"
        row = "a,b,c,d,e,f,g,h\n"
        f.write_text(row * (5 * 1024 * 1024 // len(row)))  # > 4 MiB
        assert f.stat().st_size > DundersApp._HEX_VIEW_SIZE_THRESHOLD
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            windows = list(app.desktop.windows)
            assert any(isinstance(w.content, CsvViewerContent) for w in windows)
            assert not any(isinstance(w.content, HexViewerContent) for w in windows)

    async def test_f3_on_huge_csv_falls_back_to_hex(self, tmp_path, monkeypatch):
        """Beyond the mmap cap a large CSV must NOT open as a table — it falls
        through to the same routing as any file, i.e. hex once it's > 4 MiB."""
        from dunders.app import DundersApp
        from dunders.fm.hex_viewer import HexViewerContent

        # Drop the mmap cap below the hex threshold so a >4 MiB CSV is "too big
        # to tabulate" yet still hits the hex branch — without a 256 MiB file.
        monkeypatch.setattr(DundersApp, "_CSV_MMAP_SIZE_THRESHOLD", 1024 * 1024)
        f = tmp_path / "huge.csv"
        row = "a,b,c,d,e,f,g,h\n"
        f.write_text(row * (5 * 1024 * 1024 // len(row)))  # > 4 MiB
        assert f.stat().st_size > DundersApp._HEX_VIEW_SIZE_THRESHOLD
        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._open_editor_window(f, read_only=True)
            await pilot.pause()
            windows = list(app.desktop.windows)
            assert any(isinstance(w.content, HexViewerContent) for w in windows)
            assert not any(isinstance(w.content, CsvViewerContent) for w in windows)


class TestMmapSource:
    async def test_from_path_lazy_table(self, tmp_path):
        """from_path renders correct rows/cols without reading the whole file
        into a single string."""
        from textual.app import App

        f = tmp_path / "data.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n")

        content = CsvViewerContent.from_path(f)

        class _Host(App):
            def compose(self):
                yield content

        app = _Host()
        async with app.run_test():
            assert content.widget.n_rows == 3   # header + 2 data lines
            assert content.widget.n_cols == 2
            line = "".join(seg.text for seg in content.widget._render_table_line(1))
            assert "Alice" in line and "30" in line

    def test_mmap_source_line_index(self, tmp_path):
        from dunders.fm.csv_viewer import _MmapSource

        f = tmp_path / "x.csv"
        f.write_text("a,b\n1,2\n3,4\n")  # trailing newline
        src = _MmapSource(f)
        try:
            assert src.line_count() == 3
            assert src.line(0) == "a,b"
            assert src.line(2) == "3,4"
        finally:
            src.close()

    def test_mmap_source_no_trailing_newline(self, tmp_path):
        from dunders.fm.csv_viewer import _MmapSource

        f = tmp_path / "x.csv"
        f.write_text("a,b\n1,2")  # no trailing newline
        src = _MmapSource(f)
        try:
            assert src.line_count() == 2
            assert src.line(1) == "1,2"
        finally:
            src.close()

    def test_mmap_source_incremental_index(self, tmp_path, monkeypatch):
        """Open indexes only a prefix; the rest fills in via index_batch, and a
        lazy line() pulls the index forward on demand."""
        import dunders.fm.csv_viewer as cv
        import dunders.fm.line_source as ls

        monkeypatch.setattr(ls, "PREFIX_INDEX_LINES", 10)
        f = tmp_path / "big.csv"
        f.write_text("".join(f"r{i},v{i}\n" for i in range(5000)))
        src = cv._MmapSource(f)
        try:
            # Only the prefix is indexed at open — not the whole file.
            assert not src.is_complete()
            assert src.line_count() < 5000
            # A line far past the prefix is reachable lazily (index runs forward).
            assert src.line(4000) == "r4000,v4000"
            # Draining index_batch completes the count exactly.
            while src.index_batch(1000):
                pass
            assert src.is_complete()
            assert src.line_count() == 5000
            assert src.line(4999) == "r4999,v4999"
        finally:
            src.close()
