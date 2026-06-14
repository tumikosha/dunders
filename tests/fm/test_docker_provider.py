"""DockerProvider — CLI-backed VFS provider (local + remote over SSH). Tests
mock the `_run` / subprocess seam so no real Docker daemon or SSH is needed.

Addressing: root = endpoint ("" local, "ssh://…" remote), parts[0] = container,
parts[1:] = path inside it; parts=() is the container index.
"""

import pytest

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import (
    ProviderAction,
    ProviderActions,
    ProviderColumns,
)
from dunders.fm.file_entry import FileEntry
from dunders.fm.providers.docker_provider import (
    DockerProvider,
    _parse_endpoint,
    _parse_spec,
)
from dunders.fm.vfs_local import default_registry


def _provider(run_map):
    """A DockerProvider whose _run returns canned output keyed by argv prefix."""
    p = DockerProvider()

    def fake_run(args, *, endpoint="", input=None):
        for key, out in run_map.items():
            if args[: len(key)] == list(key):
                return out
        raise AssertionError(f"unexpected docker args: {args}")

    p._run = fake_run  # type: ignore[assignment]
    return p


def _index(endpoint=""):
    return VfsPath(scheme="docker", root=endpoint, parts=())


def _container(name, *path, endpoint=""):
    return VfsPath(scheme="docker", root=endpoint, parts=(name, *path))


# ---- spec / endpoint parsing ---------------------------------------------

class TestParseSpec:
    def test_empty_spec_is_index(self):
        assert _parse_spec("") == ("", ())

    def test_name_only(self):
        assert _parse_spec("web") == ("", ("web",))

    def test_name_with_path(self):
        assert _parse_spec("web/etc/nginx") == ("", ("web", "etc", "nginx"))

    def test_leading_slash_and_scheme_stripped(self):
        assert _parse_spec("docker:web/var/log") == ("", ("web", "var", "log"))
        assert _parse_spec("//web/srv") == ("", ("web", "srv"))

    def test_whitespace_padded(self):
        assert _parse_spec("  web/srv  ") == ("", ("web", "srv"))

    def test_all_slashes_is_index(self):
        assert _parse_spec("/") == ("", ())

    def test_remote_index(self):
        assert _parse_spec("docker:ssh://u@h:22") == ("ssh://u@h:22", ())

    def test_remote_container_and_path(self):
        assert _parse_spec("ssh://u@h:22/web/etc") == ("ssh://u@h:22", ("web", "etc"))

    def test_remote_no_user_no_port(self):
        assert _parse_spec("ssh://host/web") == ("ssh://host", ("web",))


class TestParseEndpoint:
    def test_user_host_port(self):
        assert _parse_endpoint("ssh://bob@h:2222") == ("bob@h", "2222")

    def test_host_only(self):
        assert _parse_endpoint("ssh://h") == ("h", None)

    def test_non_numeric_port_kept_in_host(self):
        assert _parse_endpoint("ssh://h:weird") == ("h:weird", None)


# ---- transport routing ----------------------------------------------------

class _FakeProc:
    def __init__(self, stdout=b"", code=0, stderr=b""):
        self.stdout, self.returncode, self.stderr = stdout, code, stderr


class TestTransport:
    def test_local_run_uses_docker_binary(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return _FakeProc(b"ok")

        monkeypatch.setattr(dp.subprocess, "run", fake_run)
        assert DockerProvider()._run(["ps"]) == b"ok"
        assert seen["cmd"][0] == "docker" and seen["cmd"][1] == "ps"

    def test_remote_run_builds_multiplexed_ssh(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        calls = []
        monkeypatch.setattr(dp.subprocess, "run",
                            lambda cmd, **kw: calls.append(cmd) or _FakeProc(b"ok"))
        out = DockerProvider()._run(["ps", "-a"], endpoint="ssh://user@host:2222")
        assert out == b"ok"
        ssh = calls[-1]  # last call is docker-over-ssh (an earlier one is the master)
        assert ssh[0] == "ssh"
        assert "user@host" in ssh
        assert "-p" in ssh and "2222" in ssh
        assert any(c.startswith("ControlPath=") for c in ssh)  # multiplexed
        assert ssh[-1] == "docker ps -a"

    def test_remote_run_shlex_quotes_args(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        calls = []
        monkeypatch.setattr(dp.subprocess, "run",
                            lambda cmd, **kw: calls.append(cmd) or _FakeProc(b""))
        DockerProvider()._run(["exec", "web", "cat", "--", "/a b.txt"],
                              endpoint="ssh://h")
        assert calls[-1][-1] == "docker exec web cat -- '/a b.txt'"

    def test_master_started_once_per_endpoint(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        masters = []

        def fake_run(cmd, **kw):
            if "-M" in cmd:
                masters.append(cmd)
            return _FakeProc(b"")

        monkeypatch.setattr(dp.subprocess, "run", fake_run)
        p = DockerProvider()
        p._run(["ps"], endpoint="ssh://h")
        p._run(["ps"], endpoint="ssh://h")
        assert len(masters) == 1  # idempotent master setup

    def test_nonzero_exit_raises_oserror(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        monkeypatch.setattr(dp.subprocess, "run",
                            lambda cmd, **kw: _FakeProc(b"", code=1, stderr=b"boom"))
        with pytest.raises(OSError) as ei:
            DockerProvider()._run(["ps"])
        assert "boom" in str(ei.value)


# ---- index scan -----------------------------------------------------------

_PS_JSON = (
    b'{"ID":"abc123","Names":"web","State":"running","Status":"Up 2 hours"}\n'
    b'{"ID":"def456","Names":"db","State":"exited","Status":"Exited (0) 1h ago"}\n'
)


class TestScanIndex:
    def test_lists_containers_with_state(self):
        p = _provider({("ps", "-a"): _PS_JSON})
        rows = p.scan(_index())
        assert [e.name for e in rows] == ["web", "db"]
        assert all(e.is_dir for e in rows)

    def test_index_has_no_parent_entry(self):
        p = _provider({("ps", "-a"): _PS_JSON})
        assert all(e.name != ".." for e in p.scan(_index()))

    def test_container_entry_loc_is_parts0(self):
        p = _provider({("ps", "-a"): _PS_JSON})
        web = p.scan(_index())[0]
        assert web.loc == _container("web")

    def test_remote_index_entries_carry_endpoint(self):
        p = _provider({("ps", "-a"): _PS_JSON})
        web = p.scan(_index("ssh://h"))[0]
        assert web.loc == _container("web", endpoint="ssh://h")


# ---- filesystem scan ------------------------------------------------------

_LS = (
    b"total 12\n"
    b"drwxr-xr-x 2 root root 4096 2026-06-14 10:00:00.000000000 +0000 conf.d\n"
    b"-rw-r--r-- 1 root root  120 2026-06-14 09:30:00.000000000 +0000 nginx.conf\n"
    b"lrwxrwxrwx 1 root root   11 2026-06-14 09:00:00.000000000 +0000 link -> nginx.conf\n"
    b"-rw-r--r-- 1 root root    5 2026-06-14 09:00:00.000000000 +0000 a b.txt\n"
)
_RUNNING = b"true\n"
_STOPPED = b"false\n"


def _fs_provider(ls_out=_LS, running=_RUNNING):
    return _provider({
        ("inspect", "-f", "{{.State.Running}}"): running,
        ("exec",): ls_out,
    })


class TestScanFilesystem:
    def test_parses_dir_file_symlink(self):
        p = _fs_provider()
        by = {e.name: e for e in p.scan(_container("web", "etc", "nginx"))}
        assert by["conf.d"].is_dir
        assert by["nginx.conf"].size == 120 and not by["nginx.conf"].is_dir
        assert by["link"].is_symlink
        assert "a b.txt" in by

    def test_prepends_parent_entry(self):
        p = _fs_provider()
        rows = p.scan(_container("web", "etc"))
        assert rows[0].name == ".."
        assert rows[0].loc == _container("web")

    def test_parent_of_container_root_is_index(self):
        p = _fs_provider()
        rows = p.scan(_container("web"))
        assert rows[0].name == ".."
        assert rows[0].loc == _index()

    def test_stopped_container_raises(self):
        p = _fs_provider(running=_STOPPED)
        with pytest.raises(OSError) as ei:
            p.scan(_container("db"))
        assert "not running" in str(ei.value)

    def test_hidden_filtered_unless_requested(self):
        ls = _LS + (b"-rw-r--r-- 1 root root 0 2026-06-14 09:00:00.000000000 "
                    b"+0000 .secret\n")
        p = _fs_provider(ls_out=ls)
        loc = _container("web")
        assert ".secret" not in {e.name for e in p.scan(loc)}
        assert ".secret" in {e.name for e in p.scan(loc, show_hidden=True)}


# ---- file ops -------------------------------------------------------------

class TestFileOps:
    def test_is_dir_index_and_container_root(self):
        p = _fs_provider()
        assert p.is_dir(_index()) is True
        assert p.is_dir(_container("web")) is True

    def test_open_read_uses_exec_cat(self):
        seen = {}

        def fake_run(args, *, endpoint="", input=None):
            seen["args"] = args
            return b"file-bytes"

        p = DockerProvider()
        p._run = fake_run  # type: ignore[assignment]
        data = p.open_read(_container("web", "etc", "x")).read()
        assert data == b"file-bytes"
        assert seen["args"] == ["exec", "web", "cat", "--", "/etc/x"]

    def test_mkdir_uses_exec_mkdir_p(self):
        seen = {}

        def fake_run(args, *, endpoint="", input=None):
            seen["args"] = args
            return b""

        p = DockerProvider()
        p._run = fake_run  # type: ignore[assignment]
        p.mkdir(_container("web", "etc"), "new")
        assert seen["args"] == ["exec", "web", "mkdir", "-p", "/etc/new"]

    def test_delete_runs_rm_rf_per_target(self):
        calls = []

        def fake_run(args, *, endpoint="", input=None):
            calls.append(args)
            return b""

        p = DockerProvider()
        p._run = fake_run  # type: ignore[assignment]
        res = p.delete([_container("web", "a"), _container("web", "b")])
        assert calls == [
            ["exec", "web", "rm", "-rf", "--", "/a"],
            ["exec", "web", "rm", "-rf", "--", "/b"],
        ]
        assert res.errors == []


class TestWriteAndResolve:
    def test_open_write_packs_tar_and_cp(self):
        seen = {}

        def fake_run(args, *, endpoint="", input=None):
            seen["args"] = args
            seen["input"] = input
            return b""

        p = DockerProvider()
        p._run = fake_run  # type: ignore[assignment]
        w = p.open_write(_container("web", "etc", "new.txt"), overwrite=True)
        w.write(b"hello")
        w.close()
        assert seen["args"] == ["cp", "-", "web:/etc"]
        import io as _io
        import tarfile as _tar
        tf = _tar.open(fileobj=_io.BytesIO(seen["input"]))
        assert tf.getnames() == ["new.txt"]
        assert tf.extractfile("new.txt").read() == b"hello"

    def test_resolve_empty_spec_is_index(self):
        loc = DockerProvider().resolve_target("", base=VfsPath.local("/"))
        assert loc == _index()

    def test_resolve_running_name(self):
        p = _provider({("inspect", "-f", "{{.State.Running}}"): _RUNNING})
        assert p.resolve_target("web/etc", base=VfsPath.local("/")) == \
            _container("web", "etc")

    def test_resolve_remote_index_no_running_check(self):
        loc = DockerProvider().resolve_target("ssh://u@h", base=VfsPath.local("/"))
        assert loc == _index("ssh://u@h")

    def test_resolve_remote_container(self):
        p = _provider({("inspect", "-f", "{{.State.Running}}"): _RUNNING})
        assert p.resolve_target("ssh://u@h/web", base=VfsPath.local("/")) == \
            _container("web", endpoint="ssh://u@h")

    def test_resolve_stopped_raises(self):
        p = _provider({("inspect", "-f", "{{.State.Running}}"): _STOPPED})
        with pytest.raises(OSError):
            p.resolve_target("db", base=VfsPath.local("/"))

    def test_needs_password_false(self):
        assert DockerProvider().needs_password("web") is False

    def test_copy_move_within_return_none(self):
        p = DockerProvider()
        assert p.copy_within([], _container("web")) is None
        assert p.move_within([], _container("web")) is None


class TestRegistration:
    def test_registered_when_available(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        monkeypatch.setattr(dp, "docker_available", lambda: True)
        assert "docker" in default_registry().schemes()

    def test_absent_when_unavailable(self, monkeypatch):
        import dunders.fm.providers.docker_provider as dp
        monkeypatch.setattr(dp, "docker_available", lambda: False)
        assert "docker" not in default_registry().schemes()


class TestContract:
    def test_provider_action_defaults(self):
        a = ProviderAction(id="x.go", label="Go", run=lambda locs: None)
        assert a.icon == ""
        assert a.hotkey is None
        assert a.applies_to(object()) is True


_PS_STATES = (
    b'{"Names":"web","State":"running"}\n'
    b'{"Names":"db","State":"exited"}\n'
    b'{"Names":"cache","State":"paused"}\n'
)


class TestStateAndActions:
    def test_docker_satisfies_provider_actions(self):
        assert isinstance(DockerProvider(), ProviderActions)

    def test_glyph_in_extra(self):
        p = _provider({("ps", "-a"): _PS_STATES})
        by = {e.name: e for e in p.scan(_index())}
        assert by["web"].extra["docker.state"] == "running"
        assert by["web"].extra["glyph"]
        assert by["web"].extra["glyph_role"] == "success"
        assert by["db"].extra["glyph_role"] == "muted"
        assert by["cache"].extra["glyph_role"] == "warning"

    def test_actions_ids(self):
        ids = {a.id for a in DockerProvider().actions()}
        assert {"docker.start", "docker.stop", "docker.restart",
                "docker.remove", "docker.rebuild"} <= ids

    def test_start_applies_only_to_stopped(self):
        acts = {a.id: a for a in DockerProvider().actions()}
        stopped = FileEntry(loc=_container("db"), name="db", size=0, mtime=0.0,
                            is_dir=True, extra={"docker.state": "exited"})
        running = FileEntry(loc=_container("web"), name="web", size=0, mtime=0.0,
                            is_dir=True, extra={"docker.state": "running"})
        assert acts["docker.start"].applies_to(stopped) is True
        assert acts["docker.start"].applies_to(running) is False
        assert acts["docker.stop"].applies_to(running) is True

    def test_run_start_issues_docker_start(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append((args, endpoint)) or b""  # type: ignore
        act = {a.id: a for a in p.actions()}["docker.start"]
        act.run([_container("web")])
        assert calls == [(["start", "web"], "")]

    def test_run_start_on_remote_passes_endpoint(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append((args, endpoint)) or b""  # type: ignore
        act = {a.id: a for a in p.actions()}["docker.start"]
        act.run([_container("web", endpoint="ssh://h")])
        assert calls == [(["start", "web"], "ssh://h")]

    def test_compose_flag_from_ps_labels_no_subprocess_in_applies_to(self):
        ps = (
            b'{"Names":"web","State":"running",'
            b'"Labels":"com.docker.compose.project=demo,foo=bar"}\n'
            b'{"Names":"lonely","State":"running","Labels":"foo=bar"}\n'
        )
        p = _provider({("ps", "-a"): ps})
        by = {e.name: e for e in p.scan(_index())}
        assert by["web"].extra.get("docker.compose") == "1"
        assert "docker.compose" not in by["lonely"].extra
        rebuild = {a.id: a for a in p.actions()}["docker.rebuild"]
        assert rebuild.applies_to(by["web"]) is True
        assert rebuild.applies_to(by["lonely"]) is False


class TestColumns:
    def test_docker_satisfies_provider_columns(self):
        assert isinstance(DockerProvider(), ProviderColumns)

    def test_columns_only_at_index(self):
        p = DockerProvider()
        assert [c.label for c in p.columns(_index())] == ["S"]
        assert [c.label for c in p.columns(_index("ssh://h"))] == ["S"]  # remote too
        assert p.columns(_container("web")) == []  # inside a container

    def test_state_column_value_is_glyph_and_sort_orders_running_first(self):
        col = DockerProvider().columns(_index())[0]
        running = FileEntry(loc=_container("a"), name="a", size=0, mtime=0.0,
                            is_dir=True, extra={"docker.state": "running", "glyph": "▶"})
        stopped = FileEntry(loc=_container("b"), name="b", size=0, mtime=0.0,
                            is_dir=True, extra={"docker.state": "exited", "glyph": "■"})
        assert col.value(running) == "▶"
        assert col.sort_key(running) < col.sort_key(stopped)


class TestOpenHint:
    def test_docker_declares_open_placeholder(self):
        assert "container" in DockerProvider.open_placeholder.lower()
        assert "ssh://" in DockerProvider.open_placeholder

    def test_provider_hint_reads_str_and_callable_and_tolerates_failure(self):
        from dunders.app import DundersApp

        class _Str:
            open_placeholder = "hi"

        class _Callable:
            def open_placeholder(self):
                return "yo"

        class _Boom:
            def open_placeholder(self):
                raise RuntimeError("nope")

        hint = DundersApp._provider_hint
        assert hint(_Str(), "open_placeholder") == "hi"
        assert hint(_Callable(), "open_placeholder") == "yo"
        assert hint(_Boom(), "open_placeholder") == ""
        assert hint(object(), "open_placeholder") == ""

    def test_new_file_dialog_passes_placeholder_to_input(self):
        from dunders.fm.dialogs import NewFileDialog

        dlg = NewFileDialog(prompt="x", placeholder="hint here")
        assert dlg._input.placeholder == "hint here"
