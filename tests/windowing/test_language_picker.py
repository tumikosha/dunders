from pathlib import Path

from pygments.lexers import get_lexer_by_name

from dunders.app import DundersApp
from dunders.windowing.editor.content import EditorContent
from dunders.windowing.editor.language_picker import (
    LanguagePickerContent,
    language_entries,
)


def test_language_entries_sorted_nonempty_and_known():
    entries = language_entries()
    assert len(entries) > 100
    names = [n for n, _ in entries]
    assert names == sorted(names, key=str.lower)
    aliases = {a for _, a in entries}
    assert {"python", "json", "rust", "go"} <= aliases


def test_picked_aliases_are_valid_lexer_names():
    # Every primary alias must be loadable — that's what set_language receives.
    for _name, alias in language_entries()[:50]:
        get_lexer_by_name(alias)  # raises ClassNotFound if invalid


def test_filter_narrows_by_name_and_alias():
    picker = LanguagePickerContent(editor=None)
    picker.query = "rust"
    results = picker.filtered
    assert results
    assert all("rust" in n.lower() or "rust" in a.lower() for n, a in results)
    assert any(a == "rust" for _, a in results)


def test_empty_query_returns_all():
    picker = LanguagePickerContent(editor=None)
    assert picker.filtered == language_entries()


async def test_picker_sets_editor_language(tmp_path):
    f = tmp_path / "note.unknownext"
    f.write_text('{"a": 1}\n')
    # `editor` launch mode mounts a placeholder; open a real editor window so
    # we have a live EditorContent whose highlighter we can assert on.
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app._open_editor_window(Path(f))
        await pilot.pause()
        editor = app.query_one(EditorContent)
        app.action_set_language(editor)
        await pilot.pause()
        assert app.query_one(LanguagePickerContent) is not None
        # Type a precise filter then pick.
        for ch in "json":
            await pilot.press(ch)
        await pilot.pause()
        # The top filtered entry's alias should be a json-ish lexer.
        await pilot.press("enter")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "json" in editor._editor._highlighter.language_name.lower()
