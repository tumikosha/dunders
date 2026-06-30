from dunders.windowing.core.highlight import SyntaxHighlighter, token_to_role


def test_detect_by_filename_python():
    h = SyntaxHighlighter()
    h.detect("foo.py", "x = 1\n")
    assert h.enabled
    assert "Python" in h.language_name


def test_unknown_language_is_noop():
    h = SyntaxHighlighter()
    h.detect("foo.unknownext", "")
    assert not h.enabled
    assert h.tokenize(["anything", "here"]) == [[], []]


def test_guess_lexer_rejects_low_confidence_prose():
    # Prose / URL-heavy text has no real lexer. guess_lexer would otherwise
    # pick a noise-floor lexer (e.g. "Tera Term macro", confidence ~0.01) that
    # fragments every URL into dozens of syntax spans, making render_line emit
    # ~one Rich segment per URL component on every scroll.
    h = SyntaxHighlighter()
    prose = "see https://example.com/path?q=1 and http://foo.bar/baz for details\n" * 30
    h.detect(None, prose)
    assert not h.enabled
    lines = prose.splitlines()
    assert h.tokenize(lines) == [[] for _ in lines]


def test_guess_lexer_accepts_confident_code():
    h = SyntaxHighlighter()
    code = "import os\n\ndef main(x):\n    for i in range(x):\n        print(i)\n"
    h.detect(None, code)  # no filename → content guess
    assert h.enabled
    assert "Python" in h.language_name


def test_tokenize_python_keyword_and_string():
    h = SyntaxHighlighter()
    h.detect("foo.py", "")
    spans = h.tokenize(["def f():", "    return 'hi'"])
    roles_line0 = {s.role for s in spans[0]}
    roles_line1 = {s.role for s in spans[1]}
    assert "keyword" in roles_line0          # `def`
    assert "function" in roles_line0         # `f`
    assert "keyword" in roles_line1          # `return`
    assert "string" in roles_line1           # 'hi'
    # spans stay within their own line's column range
    line1 = "    return 'hi'"
    for s in spans[1]:
        assert 0 <= s.start <= s.end <= len(line1)


def test_tokenize_preserves_line_count_and_blank_lines():
    h = SyntaxHighlighter()
    h.detect("foo.py", "")
    lines = ["x = 1", "", "y = 2"]
    spans = h.tokenize(lines)
    assert len(spans) == 3
    assert spans[1] == []  # blank line, no spans


def test_set_language_override():
    h = SyntaxHighlighter()
    h.detect("foo.txt", "")          # likely Text lexer / no useful tokens
    h.set_language("json")
    assert h.enabled
    spans = h.tokenize(['{"a": 1}'])
    assert any(s.role in {"string", "number", "name"} for s in spans[0])


def test_token_to_role_collapses_subtypes():
    from pygments.token import Keyword, Name, Comment
    assert token_to_role(Keyword.Namespace) == "keyword"
    assert token_to_role(Name.Function) == "function"
    assert token_to_role(Comment.Single) == "comment"
    assert token_to_role(Name) == "name"


def test_tokenize_exact_column_positions():
    h = SyntaxHighlighter()
    h.detect("foo.py", "")
    spans = {(s.start, s.end): s.role for s in h.tokenize(["def f():"])[0]}
    assert spans[(0, 3)] == "keyword"   # def
    assert spans[(4, 5)] == "function"  # f


def test_set_language_unknown_keeps_previous():
    h = SyntaxHighlighter()
    h.detect("foo.py", "")
    h.set_language("notareallanguagexyz")
    assert "Python" in h.language_name


def test_tokenize_multiline_string():
    h = SyntaxHighlighter()
    h.detect("foo.py", "")
    lines = ['s = """', 'middle', '"""']
    spans = h.tokenize(lines)
    assert any(s.role == "string" for s in spans[0])
    assert any(s.role == "string" and s.start == 0 for s in spans[1])
    assert any(s.role == "string" and s.start == 0 for s in spans[2])


def test_palette_has_syntax_roles():
    from dunders.windowing.themes.modern_dark import modern_dark
    for role in (
        "keyword", "name", "function", "class", "string",
        "number", "comment", "operator", "builtin", "error",
    ):
        style = modern_dark.resolve(f"editor.syntax.{role}")
        assert style.fg is not None, f"editor.syntax.{role} has no colour"


def test_should_highlight_threshold(monkeypatch):
    from dunders.windowing.editor import widget as widget_mod
    from dunders.windowing.editor.widget import EditorWidget
    from dunders.windowing.core.buffer import TextBuffer

    buf = TextBuffer.from_string("x = 1\n")
    buf.file_path = "foo.py"
    ed = EditorWidget(buffer=buf)
    ed._highlighter.detect("foo.py", "x = 1\n")
    assert ed._should_highlight() is True

    # Shrink the threshold below the buffer size → highlighting disabled.
    monkeypatch.setattr(widget_mod, "_SYNTAX_SIZE_THRESHOLD", 1)
    assert ed._should_highlight() is False
