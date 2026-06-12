from pathlib import Path

from dunders.fm.file_panel import FilePanel


def test_file_panel_stores_cwd_as_path():
    panel = FilePanel(cwd="/tmp")
    assert isinstance(panel.cwd, Path)
    # Intentionally compares against Path("/tmp"), not Path("/tmp").resolve():
    # FilePanel uses expanduser() only (not resolve()) to preserve the
    # user-supplied path form for the title bar.
    assert panel.cwd == Path("/tmp")


def test_file_panel_window_title_matches_cwd():
    panel = FilePanel(cwd="/tmp/foo")
    # WindowContent exposes window_title as a reactive attribute that the
    # enclosing Window mirrors on the title bar. The stub publishes the cwd
    # as the title so an empty-content panel is still recognisable.
    assert panel.window_title == "/tmp/foo"
