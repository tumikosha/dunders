import json
from dunders.windowing.core.macro import MacroRecorder, MacroAction, MacroStorage


class TestMacroRecorder:
    def test_not_recording_by_default(self):
        rec = MacroRecorder()
        assert rec.is_recording is False

    def test_start_recording(self):
        rec = MacroRecorder()
        rec.start_recording()
        assert rec.is_recording is True

    def test_stop_recording_returns_actions(self):
        rec = MacroRecorder()
        rec.start_recording()
        rec.record_action(MacroAction(kind="keypress", data="a"))
        rec.record_action(MacroAction(kind="keypress", data="b"))
        actions = rec.stop_recording()
        assert len(actions) == 2
        assert actions[0].data == "a"
        assert actions[1].data == "b"
        assert rec.is_recording is False

    def test_record_ignores_when_not_recording(self):
        rec = MacroRecorder()
        rec.record_action(MacroAction(kind="keypress", data="a"))
        rec.start_recording()
        actions = rec.stop_recording()
        assert actions == []

    def test_toggle_recording(self):
        rec = MacroRecorder()
        rec.toggle_recording()
        assert rec.is_recording is True
        rec.record_action(MacroAction(kind="keypress", data="x"))
        actions = rec.toggle_recording()
        assert rec.is_recording is False
        assert len(actions) == 1


class TestMacroStorage:
    def test_save_and_load_macro(self, tmp_path):
        storage = MacroStorage(config_dir=str(tmp_path))
        actions = [
            MacroAction(kind="keypress", data="a"),
            MacroAction(kind="command", data="goto 10"),
        ]
        storage.save_macro("test_macro", "ctrl+1", actions, permanent=True)
        loaded = storage.load_macros(permanent=True)
        assert "test_macro" in loaded
        assert loaded["test_macro"]["key"] == "ctrl+1"
        assert len(loaded["test_macro"]["actions"]) == 2

    def test_session_macros_separate_from_permanent(self, tmp_path):
        storage = MacroStorage(config_dir=str(tmp_path))
        actions = [MacroAction(kind="keypress", data="x")]
        storage.save_macro("sess", "ctrl+2", actions, permanent=False)
        storage.save_macro("perm", "ctrl+3", actions, permanent=True)
        session = storage.load_macros(permanent=False)
        permanent = storage.load_macros(permanent=True)
        assert "sess" in session
        assert "sess" not in permanent
        assert "perm" in permanent
        assert "perm" not in session

    def test_delete_macro(self, tmp_path):
        storage = MacroStorage(config_dir=str(tmp_path))
        actions = [MacroAction(kind="keypress", data="a")]
        storage.save_macro("to_delete", "ctrl+4", actions, permanent=True)
        storage.delete_macro("to_delete", permanent=True)
        loaded = storage.load_macros(permanent=True)
        assert "to_delete" not in loaded

    def test_list_all_macros(self, tmp_path):
        storage = MacroStorage(config_dir=str(tmp_path))
        actions = [MacroAction(kind="keypress", data="a")]
        storage.save_macro("s1", "ctrl+1", actions, permanent=False)
        storage.save_macro("p1", "ctrl+2", actions, permanent=True)
        all_macros = storage.list_all()
        names = [m["name"] for m in all_macros]
        assert "s1" in names
        assert "p1" in names
