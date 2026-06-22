"""Pure NL→command helpers: prompt building and suggestion parsing."""

from __future__ import annotations

from dunders.fm.nl_command import build_prompt, parse_suggestion


def test_build_prompt_includes_context():
    p = build_prompt("find big files", "/home/u/proj", "Darwin (arm64)")
    assert "find big files" in p
    assert "/home/u/proj" in p
    assert "Darwin (arm64)" in p


def test_parse_markers():
    cmd, why = parse_suggestion("CMD: ls -la\nWHY: lists files")
    assert cmd == "ls -la"
    assert why == "lists files"


def test_parse_case_insensitive_and_trim():
    cmd, why = parse_suggestion("cmd:   find . -name '*.py'   \nwhy:  finds python  ")
    assert cmd == "find . -name '*.py'"
    assert why == "finds python"


def test_parse_fallback_no_markers():
    cmd, why = parse_suggestion("ls -la")
    assert cmd == "ls -la"
    assert why == ""


def test_parse_strips_code_fence():
    cmd, _ = parse_suggestion("```sh\nls -la\n```")
    assert cmd == "ls -la"


def test_parse_strips_backticks_and_dollar():
    assert parse_suggestion("CMD: `ls`")[0] == "ls"
    assert parse_suggestion("$ ls -la")[0] == "ls -la"


def test_parse_empty():
    assert parse_suggestion("") == ("", "")


def test_parse_prefers_cmd_line_over_first_line():
    cmd, why = parse_suggestion("Here you go:\nCMD: du -sh *\nWHY: sizes")
    assert cmd == "du -sh *"
    assert why == "sizes"
