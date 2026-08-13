from pathlib import Path

from dunders.main import (
    _parse_args,
    _resolve_launch_mode,
    _resolve_project_dir,
    _resolve_we_args,
    main,
    main_we,
)


# ---------------------------------------------------------------------------
# _resolve_we_args — new unified resolver
# ---------------------------------------------------------------------------

def test_resolve_we_args_no_paths_falls_back_to_we_mc():
    # No file argument -> mc-style file manager, relay mode.
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args([])
    assert launch_mode == "we-mc"
    assert initial_path is None
    assert files == []
    assert terminal_mode == "relay"


def test_resolve_we_args_directory_opens_mc_seeded(tmp_path):
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args([str(tmp_path)])
    assert launch_mode == "we-mc"
    assert initial_path == str(tmp_path)
    assert files == []
    assert terminal_mode == "relay"


def test_resolve_we_args_files_use_we_mode():
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args(["a.py", "b.py"])
    assert launch_mode == "we"
    assert initial_path is None
    assert files == ["a.py", "b.py"]
    assert terminal_mode == "relay"


# ---------------------------------------------------------------------------
# New tests from task spec
# ---------------------------------------------------------------------------

def test_we_no_args_is_mc_relay():
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args([])
    assert launch_mode == "we-mc"
    assert initial_path is None
    assert files == []
    assert terminal_mode == "relay"


def test_we_suspend_flag_is_mc_suspend():
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args(["--suspend"])
    assert launch_mode == "we-mc"
    assert terminal_mode == "suspend"


def test_we_directory_arg_is_mc_seeded(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args([str(d)])
    assert launch_mode == "we-mc"
    assert initial_path == str(d)
    assert files == []


def test_we_files_are_editor_cascade(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args([str(f)])
    assert launch_mode == "we"
    assert files == [str(f)]


def test_wew_forces_suspend(tmp_path):
    launch_mode, initial_path, files, terminal_mode, _pd = _resolve_we_args(["--suspend"])
    assert (launch_mode, terminal_mode) == ("we-mc", "suspend")


# ---------------------------------------------------------------------------
# main_we integration (monkeypatched DundersApp)
# ---------------------------------------------------------------------------

def _capture_app(monkeypatch):
    captured = {}

    class _FakeApp:
        def __init__(self, *, launch_mode, initial_path=None, initial_paths=None,
                     terminal_mode=None, project_dir=None):
            captured["launch_mode"] = launch_mode
            captured["initial_path"] = initial_path
            captured["initial_paths"] = initial_paths
            captured["terminal_mode"] = terminal_mode
            captured["project_dir"] = project_dir

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


# ---------------------------------------------------------------------------
# --pd — the Project View tree root
# ---------------------------------------------------------------------------

def test_editor_launch_without_pd_uses_the_cwd(tmp_path, monkeypatch):
    """`__ FILE` run from a project root shows that root, not the file's dir."""
    root = tmp_path / "project"
    (root / "deep" / "nested").mkdir(parents=True)
    f = root / "deep" / "nested" / "a.py"
    f.write_text("x = 1\n")
    monkeypatch.chdir(root)

    assert _resolve_project_dir(None, "editor", str(f)) == str(root)


def test_empty_pd_falls_back_to_the_files_directory(tmp_path):
    f = tmp_path / "deep" / "a.py"
    f.parent.mkdir(parents=True)
    f.write_text("x = 1\n")

    assert _resolve_project_dir("", "editor", str(f)) == str(f.parent)


def test_empty_pd_without_a_file_is_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _resolve_project_dir("", "we-mc", None) == str(tmp_path)


def test_explicit_pd_wins_over_everything(tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert _resolve_project_dir(str(other), "editor", "/some/file.py") == str(other)
    # …and applies to a file-manager launch too.
    assert _resolve_project_dir(str(other), "fm", None) == str(other)


def test_fm_launch_without_pd_keeps_deriving_from_the_positional_path():
    assert _resolve_project_dir(None, "fm", None) is None
    assert _resolve_project_dir(None, "we-mc", None) is None


def test_we_files_default_the_project_dir_to_the_cwd(tmp_path, monkeypatch):
    f = tmp_path / "sub" / "a.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)

    launch_mode, _ip, files, _tm, project_dir = _resolve_we_args([str(f)])
    assert (launch_mode, files) == ("we", [str(f)])
    assert project_dir == str(tmp_path)


def test_we_honours_an_explicit_pd(tmp_path):
    launch_mode, _ip, _files, _tm, project_dir = _resolve_we_args(
        ["--pd", str(tmp_path), "a.py"]
    )
    assert (launch_mode, project_dir) == ("we", str(tmp_path))


def test_we_empty_pd_uses_the_first_files_directory(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    _lm, _ip, _files, _tm, project_dir = _resolve_we_args(["--pd", "", str(f)])
    assert project_dir == str(tmp_path)


def test_dunders_editor_launch_passes_the_cwd_as_project_dir(monkeypatch, tmp_path):
    f = tmp_path / "nested" / "a.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    cwd = tmp_path / "root"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    captured = _capture_app(monkeypatch)
    monkeypatch.setattr("sys.argv", ["dunders", str(f)])
    main()

    assert captured["launch_mode"] == "editor"
    assert captured["initial_path"] == str(f)
    assert captured["project_dir"] == str(cwd)


def test_dunders_directory_launch_leaves_project_dir_unset(monkeypatch, tmp_path):
    captured = _capture_app(monkeypatch)
    monkeypatch.setattr("sys.argv", ["dunders", str(tmp_path)])
    main()

    assert captured["launch_mode"] == "fm"
    assert captured["initial_path"] == str(tmp_path)
    assert captured["project_dir"] is None


def test_dunders_pd_flag_is_parsed(tmp_path):
    args = _parse_args(["--pd", str(tmp_path), "a.py"])
    assert args.pd == str(tmp_path)
    assert _resolve_launch_mode(args)[0] in ("fm", "editor")


def test_app_panel_cwd_prefers_the_project_dir(tmp_path):
    from dunders.app import DundersApp

    f = tmp_path / "deep" / "a.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    root = tmp_path / "root"
    root.mkdir()

    app = DundersApp(launch_mode="editor", initial_path=str(f), project_dir=str(root))
    assert app._panel_cwd() == root

    # Without --pd the old behaviour stands: the file's own directory.
    plain = DundersApp(launch_mode="editor", initial_path=str(f))
    assert plain._panel_cwd() == Path(f.parent)


# ---------------------------------------------------------------------------
# --pd as a bare flag (nargs="?"), and the positional path argparse ate
# ---------------------------------------------------------------------------

def test_bare_pd_after_the_path_means_the_files_directory(tmp_path, monkeypatch):
    f = tmp_path / "sub" / "a.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)

    launch_mode, _ip, files, _tm, project_dir = _resolve_we_args([str(f), "--pd"])
    assert (launch_mode, files) == ("we", [str(f)])
    assert project_dir == str(f.parent)


def test_bare_pd_before_the_path_still_opens_the_file(tmp_path):
    """`__ --pd FILE` — argparse hands FILE to --pd; the file must still open."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    launch_mode, _ip, files, _tm, project_dir = _resolve_we_args(["--pd", str(f)])
    assert (launch_mode, files) == ("we", [str(f)])
    assert project_dir == str(tmp_path)


def test_pd_naming_a_file_roots_the_tree_at_its_directory(tmp_path):
    f = tmp_path / "deep" / "a.py"
    f.parent.mkdir()
    f.write_text("x = 1\n")
    other = tmp_path / "b.py"
    other.write_text("y = 2\n")

    _lm, _ip, files, _tm, project_dir = _resolve_we_args(["--pd", str(f), str(other)])
    assert files == [str(other)]
    assert project_dir == str(f.parent)


def test_bare_pd_alone_is_the_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    launch_mode, _ip, files, _tm, project_dir = _resolve_we_args(["--pd"])
    assert (launch_mode, files) == ("we-mc", [])
    assert project_dir == str(tmp_path)


def test_dunders_bare_pd_before_the_path_still_opens_the_file(monkeypatch, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    captured = _capture_app(monkeypatch)
    monkeypatch.setattr("sys.argv", ["dunders", "--pd", str(f)])
    main()

    assert captured["launch_mode"] == "editor"
    assert captured["initial_path"] == str(f)
    assert captured["project_dir"] == str(tmp_path)
