"""Help ▸ Key Probe must stay on top after the menu closes.

The menu-close handler (`_on_menu_active_index_changed`) raises whatever
window was focused before the menu opened — the FilePanel — so an action that
mounts a plain window has to claim that restore for itself, or the new window
ends up buried under the panels.
"""

from dunders.app import DundersApp
from dunders.windowing import Window


async def test_key_probe_stays_on_top_after_the_menu_closes():
    app = DundersApp(launch_mode="fm", initial_path="/tmp")
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert app.desktop is not None
        panel_win = app.desktop.focused_window
        assert panel_win is not None
        # As action_menu would capture it when the user opens Help.
        app._pre_menu_window = panel_win

        app.action_key_probe()
        # Simulate the menu closing (active_index → None).
        app._on_menu_active_index_changed(None)
        await pilot.pause()

        win = app.desktop.query_one("#key-probe", Window)
        assert app.desktop.windows[-1] is win, "panel was raised over Key Probe"
        assert app.desktop.focused_window is win


async def test_find_results_stay_on_top_after_the_menu_closes(tmp_path):
    from dunders.fm.find_file import FindOptions

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        panel_win = app.desktop.focused_window
        app._pre_menu_window = panel_win

        options = FindOptions(
            masks=("*.py",),
            case_sensitive_mask=False,
            contains="",
            case_sensitive_text=False,
            whole_words=False,
            search_for_folders=False,
            follow_symlinks=False,
        )
        app._start_search(options, tmp_path)
        app._on_menu_active_index_changed(None)
        await pilot.pause()

        win = app.desktop.query_one("#find_results", Window)
        assert app.desktop.windows[-1] is win, "panel was raised over Find results"
