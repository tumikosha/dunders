"""cd-on-exit: the --shell-init wrapper and the app's side of the contract."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dunders import shell_init


def test_snippet_defines_a_wrapper_per_command():
    code = shell_init.snippet("zsh")
    for cmd in ("dunders", "__", "__w"):
        assert f'{cmd}() {{ _dunders_cd {cmd} "$@"; }}' in code
    with pytest.raises(ValueError):
        shell_init.snippet("fish")


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_wrapper_cds_into_written_dir_and_keeps_exit_code(shell, tmp_path):
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} not installed")
    target = tmp_path / "target dir"
    target.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "__"
    fake.write_text(f'#!/bin/sh\nprintf %s "{target}" > "$DUNDERS_CWD_FILE"\nexit 3\n')
    fake.chmod(0o755)
    init = tmp_path / "init.sh"
    init.write_text(shell_init.snippet(shell))
    script = f'source "{init}"; cd /; __; echo "$?"; pwd -P'
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "TMPDIR": str(tmp_path)}
    out = subprocess.run([exe, "-c", script], capture_output=True, text=True, env=env)
    rc, pwd = out.stdout.split("\n")[:2]
    assert rc == "3"
    assert Path(pwd) == target.resolve()
    assert not list(tmp_path.glob("dunders-cwd.*"))  # temp file removed


def test_take_cwd_file_removes_var_from_environment(monkeypatch):
    monkeypatch.setenv(shell_init.ENV_VAR, "/tmp/x")
    assert shell_init.take_cwd_file() == "/tmp/x"
    assert shell_init.ENV_VAR not in os.environ
    assert shell_init.take_cwd_file() is None


def test_report_writes_chosen_dir_for_wrapper(tmp_path):
    f = tmp_path / "cwd"
    shell_init.report_exit_dir("/some/dir", str(f), tmp_path)
    assert f.read_text() == "/some/dir"


def test_report_writes_nothing_for_non_dialog_exit(tmp_path):
    f = tmp_path / "cwd"
    shell_init.report_exit_dir(None, str(f), tmp_path)
    assert not f.exists()


def test_report_without_wrapper_explains_how_to_enable(tmp_path, capsys):
    shell_init.report_exit_dir(str(tmp_path / "elsewhere"), None, tmp_path)
    assert "--shell-init" in capsys.readouterr().err
    shell_init.report_exit_dir(str(tmp_path), None, tmp_path)  # start dir: silent
    assert capsys.readouterr().err == ""
