"""Help menu: open the bundled HELP.md in the Markdown viewer."""

from pathlib import Path

import pytest

import dunders
from dunders.app import DundersApp
from dunders.fm.markdown_viewer import MarkdownViewerContent


def test_help_md_is_bundled_and_covers_topics():
    help_path = Path(dunders.__file__).parent / "HELP.md"
    assert help_path.exists(), "HELP.md must ship inside the package"
    text = help_path.read_text(encoding="utf-8").lower()
    assert "hot key" in text or "hotkey" in text
    assert "ai" in text


@pytest.mark.asyncio
async def test_help_menu_opens_markdown_viewer(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.pause()
        # command is registered and wired into the Help menu
        assert app.command_registry.get("help.show") is not None
        help_menu = next(m for m in app.menu_bar.menus if m.label == "Help")
        assert "help.show" in [getattr(i, "command_id", None) for i in help_menu.items]
        # invoking it opens a Markdown viewer window
        app.action_help_show()
        await pilot.pause()
        await pilot.pause()
        assert app.query(MarkdownViewerContent)
