from dunders.main import _resolve_we_args, main_we


# ---------------------------------------------------------------------------
# _resolve_we_args — new unified resolver
# ---------------------------------------------------------------------------

def test_resolve_we_args_no_paths_falls_back_to_we_mc():
    # No file argument -> mc-style file manager, relay mode.
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args([])
    assert launch_mode == "we-mc"
    assert initial_path is None
    assert files == []
    assert terminal_mode == "relay"


def test_resolve_we_args_directory_opens_mc_seeded(tmp_path):
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args([str(tmp_path)])
    assert launch_mode == "we-mc"
    assert initial_path == str(tmp_path)
    assert files == []
    assert terminal_mode == "relay"


def test_resolve_we_args_files_use_we_mode():
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args(["a.py", "b.py"])
    assert launch_mode == "we"
    assert initial_path is None
    assert files == ["a.py", "b.py"]
    assert terminal_mode == "relay"


# ---------------------------------------------------------------------------
# New tests from task spec
# ---------------------------------------------------------------------------

def test_we_no_args_is_mc_relay():
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args([])
    assert launch_mode == "we-mc"
    assert initial_path is None
    assert files == []
    assert terminal_mode == "relay"


def test_we_suspend_flag_is_mc_suspend():
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args(["--suspend"])
    assert launch_mode == "we-mc"
    assert terminal_mode == "suspend"


def test_we_directory_arg_is_mc_seeded(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args([str(d)])
    assert launch_mode == "we-mc"
    assert initial_path == str(d)
    assert files == []


def test_we_files_are_editor_cascade(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args([str(f)])
    assert launch_mode == "we"
    assert files == [str(f)]


def test_wew_forces_suspend(tmp_path):
    launch_mode, initial_path, files, terminal_mode = _resolve_we_args(["--suspend"])
    assert (launch_mode, terminal_mode) == ("we-mc", "suspend")


# ---------------------------------------------------------------------------
# main_we integration (monkeypatched DundersApp)
# ---------------------------------------------------------------------------

def _capture_app(monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, *, launch_mode, initial_path=None, initial_paths=None,
                     terminal_mode=None):
            captured["launch_mode"] = launch_mode
            captured["initial_path"] = initial_path
            captured["initial_paths"] = initial_paths
            captured["terminal_mode"] = terminal_mode

        def run(self):
            captured["ran"] = True

    monkeypatch.setattr("dunders.main.DundersApp", _FakeApp)
    return captured


def test_main_we_constructs_we_app(monkeypatch):
    captured = _capture_app(monkeypatch)
    monkeypatch.setattr("sys.argv", ["we", "a.py", "b.py"])
    main_we()

    assert captured["launch_mode"] == "we"
    assert captured["initial_paths"] == ["a.py", "b.py"]
    assert captured["ran"] is True


def test_main_we_without_args_opens_file_manager(monkeypatch):
    captured = _capture_app(monkeypatch)
    monkeypatch.setattr("sys.argv", ["we"])
    main_we()

    assert captured["launch_mode"] == "we-mc"
    assert captured["initial_path"] is None
    assert captured["ran"] is True


def test_main_we_with_directory_seeds_panels(monkeypatch, tmp_path):
    captured = _capture_app(monkeypatch)
    monkeypatch.setattr("sys.argv", ["we", str(tmp_path)])
    main_we()

    assert captured["launch_mode"] == "we-mc"
    assert captured["initial_path"] == str(tmp_path)
    assert captured["ran"] is True
