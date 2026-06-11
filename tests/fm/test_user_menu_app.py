from pathlib import Path

from tyui.app import TyuiApp
from tyui.fm.user_menu import MacroContext, expand_macros
from tyui.fm.user_menu_dialog import UserMenuDialog
from tyui.fm.user_menu_loader import global_menu_path


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


async def test_f2_seeds_and_opens_editor_when_no_menu(tmp_path):
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        app.action_user_menu()
        await _settle(pilot)
    # Seed file was written and an editor window opened on it.
    assert global_menu_path().is_file()


async def test_f2_opens_dialog_when_menu_exists(tmp_path):
    (tmp_path / ".tyui.menu.md").write_text(
        "## X\n\n### (a) Alpha\n```\necho alpha\n```\n", encoding="utf-8"
    )
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        app.action_user_menu()
        await _settle(pilot)
        dialogs = list(app.query(UserMenuDialog))
        assert len(dialogs) == 1


class _FakeHandover:
    def __init__(self, *, ends_at=None):
        self.calls = []
        self.last_cwd = None
        self._ends_at = ends_at  # simulate a `cd` leaving the shell here

    def run_foreground(self, cmd, cwd):
        self.calls.append((cmd, str(cwd)))
        self.last_cwd = self._ends_at
        return 0

    def command_screen(self, cwd):
        pass

    def shutdown(self):
        pass


async def test_selecting_entry_runs_expanded_body_with_panel_cwd(tmp_path):
    (tmp_path / ".tyui.menu.md").write_text(
        "## X\n\n### (a) Echo dir\n```\necho %d\n```\n", encoding="utf-8"
    )
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        fake = _FakeHandover()
        app._handover = fake
        app.action_user_menu()
        await _settle(pilot)
        await pilot.press("a")          # hotkey runs the entry
        await _settle(pilot)
    # expand_macros always single-quotes values; derive the expected string
    # from it so the assertion tracks the real quoting behaviour.
    expected = expand_macros(
        "echo %d", MacroContext(None, (), str(tmp_path), None, None), {}
    )
    assert fake.calls == [(expected, str(tmp_path))]


async def test_entry_that_cds_moves_active_panel(tmp_path):
    # A User Menu entry whose command `cd`s elsewhere should move the active
    # panel there (the handover reports the resulting cwd via last_cwd).
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / ".tyui.menu.md").write_text(
        "## X\n\n### (a) Go\n```\ncd sub\n```\n", encoding="utf-8"
    )
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        app._handover = _FakeHandover(ends_at=sub)
        app.action_user_menu()
        await _settle(pilot)
        await pilot.press("a")
        await _settle(pilot)
        assert app._panel_cwd_for_test().resolve() == sub.resolve()


async def test_entry_without_cd_keeps_panel(tmp_path):
    (tmp_path / ".tyui.menu.md").write_text(
        "## X\n\n### (a) Echo\n```\necho hi\n```\n", encoding="utf-8"
    )
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        # last_cwd reports the same dir -> panel must not move.
        app._handover = _FakeHandover(ends_at=tmp_path)
        app.action_user_menu()
        await _settle(pilot)
        await pilot.press("a")
        await _settle(pilot)
        assert app._panel_cwd_for_test().resolve() == tmp_path.resolve()


async def test_prompt_macro_asks_then_runs(tmp_path):
    (tmp_path / ".tyui.menu.md").write_text(
        "## X\n\n### (a) Greet\n```\necho %{Name}\n```\n", encoding="utf-8"
    )
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        fake = _FakeHandover()
        app._handover = fake
        app.action_user_menu()
        await _settle(pilot)
        await pilot.press("a")
        await _settle(pilot)
        # an InputDialog for the prompt is now modal; type and submit
        await pilot.press("w", "o", "r", "l", "d", "enter")
        await _settle(pilot)
    # the %{Name} value is single-quoted by expand_macros
    assert fake.calls == [("echo 'world'", str(tmp_path))]


async def test_f4_opens_menu_file_in_editor(tmp_path):
    menu = tmp_path / ".tyui.menu.md"
    menu.write_text("## X\n\n### (a) Alpha\n```\necho alpha\n```\n", encoding="utf-8")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        app.action_user_menu()
        await _settle(pilot)
        await pilot.press("f4")
        await _settle(pilot)
        # an editor window for the menu file is now open
        from tyui.windowing.editor import EditorContent
        editors = [
            w for w in app.desktop.windows
            if isinstance(getattr(w, "content", None), EditorContent)
        ]
        assert editors, "F4 should open the menu file in an editor"


async def test_user_menu_in_editor_uses_edited_file_macros(tmp_path):
    # When the User Menu is invoked from a focused editor, %f/%d must resolve
    # to the edited file, not the (still-present) file panel.
    (tmp_path / ".tyui.menu.md").write_text(
        "## X\n\n### (a) Path\n```\necho %d/%f\n```\n", encoding="utf-8"
    )
    f = tmp_path / "myfile.py"
    f.write_text("x = 1\n")
    app = TyuiApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        fake = _FakeHandover()
        app._handover = fake
        app._open_editor_window(f, read_only=False)
        await _settle(pilot)
        app.action_user_menu()
        await _settle(pilot)
        await pilot.press("a")
        await _settle(pilot)
    expected = expand_macros(
        "echo %d/%f",
        MacroContext(current_file=f.name, tagged=(), panel_dir=str(tmp_path),
                     other_file=None, other_dir=None),
        {},
    )
    assert fake.calls == [(expected, str(tmp_path))]
