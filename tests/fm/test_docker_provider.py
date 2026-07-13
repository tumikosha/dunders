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
    PreviewResult,
    ProviderPreview,
)
from dunders.fm.file_entry import FileEntry
from dunders.fm.providers.docker_provider import (
    DockerProvider,
    _parse_endpoint,
    _parse_spec,
    _container_name, _volume_name, _fs_path, _level,
    _COMPOSE, _SERVICE, _CONTAINER, _VOLUME,
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


def _v(*parts, endpoint=""):
    return VfsPath(scheme="docker", root=endpoint, parts=parts)


class TestTokens:
    def test_level_of_each_shape(self):
        assert _level(_v()) == "top"
        assert _level(_v("containers")) == "containers"
        assert _level(_v("images")) == "images"
        assert _level(_v("networks")) == "networks"
        assert _level(_v("volumes")) == "volumes"
        assert _level(_v("compose:demo")) == "compose"
        assert _level(_v("compose:demo", "service:web")) == "service"
        assert _level(_v("containers", "container:web")) == "cfs"
        assert _level(_v("compose:demo", "service:web", "container:web")) == "cfs"
        assert _level(_v("containers", "container:web", "etc")) == "cfs"
        assert _level(_v("volumes", "volume:data")) == "vfs"
        assert _level(_v("volumes", "volume:data", "sub")) == "vfs"

    def test_container_name_extracted_at_any_depth(self):
        assert _container_name(_v("containers", "container:web")) == "web"
        assert _container_name(_v("compose:d", "service:w", "container:web", "etc")) == "web"
        assert _container_name(_v("images")) == ""

    def test_volume_name_extracted(self):
        assert _volume_name(_v("volumes", "volume:data", "a")) == "data"
        assert _volume_name(_v("volumes")) == ""

    def test_fs_path_after_token(self):
        assert _fs_path(_v("containers", "container:web"), _CONTAINER) == "/"
        assert _fs_path(_v("containers", "container:web", "etc", "nginx"), _CONTAINER) == "/etc/nginx"
        assert _fs_path(_v("volumes", "volume:data", "logs"), _VOLUME) == "/logs"


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



class TestScanTop:
    _PS = (
        b'{"Names":"web","State":"running","Labels":"com.docker.compose.project=demo,com.docker.compose.service=web"}\n'
        b'{"Names":"db","State":"running","Labels":"com.docker.compose.project=demo,com.docker.compose.service=db"}\n'
        b'{"Names":"lonely","State":"running","Labels":"foo=bar"}\n'
    )

    def test_top_lists_compose_groups_then_sections(self):
        p = _provider({("ps", "-a"): self._PS})
        rows = p.scan(_v())
        names = [e.name for e in rows]
        assert "demo" in names
        assert names[-4:] == ["Containers", "Images", "Networks", "Volumes"]
        assert all(e.is_dir for e in rows)
        assert all(e.name != ".." for e in rows)

    def test_compose_group_loc_and_flag(self):
        p = _provider({("ps", "-a"): self._PS})
        demo = next(e for e in p.scan(_v()) if e.name == "demo")
        assert demo.loc == _v("compose:demo")
        assert demo.extra.get("docker.compose") == "1"

    def test_section_locs(self):
        p = _provider({("ps", "-a"): self._PS})
        by = {e.name: e for e in p.scan(_v())}
        assert by["Containers"].loc == _v("containers")
        assert by["Images"].loc == _v("images")

    def test_top_carries_endpoint(self):
        p = _provider({("ps", "-a"): self._PS})
        by = {e.name: e for e in p.scan(_v(endpoint="ssh://h"))}
        assert by["Images"].loc == _v("images", endpoint="ssh://h")


class TestScanContainerLevels:
    _PS = (
        b'{"ID":"1","Names":"web","State":"running","Status":"Up 2h","Image":"nginx",'
        b'"Labels":"com.docker.compose.project=demo,com.docker.compose.service=web"}\n'
        b'{"ID":"2","Names":"db","State":"exited","Status":"Exited","Image":"pg",'
        b'"Labels":"com.docker.compose.project=demo,com.docker.compose.service=db"}\n'
        b'{"ID":"3","Names":"lonely","State":"running","Status":"Up 1h","Image":"redis",'
        b'"Labels":"foo=bar"}\n'
    )

    def _p(self):
        return _provider({("ps", "-a"): self._PS})

    def test_containers_section_lists_only_standalone(self):
        rows = [e for e in self._p().scan(_v("containers")) if e.name != ".."]
        assert [e.name for e in rows] == ["lonely"]
        assert rows[0].loc == _v("containers", "container:lonely")
        assert rows[0].extra["docker.state"] == "running"
        assert rows[0].extra["docker.image"] == "redis"

    def test_containers_section_has_parent_to_top(self):
        rows = self._p().scan(_v("containers"))
        assert rows[0].name == ".." and rows[0].loc == _v()

    def test_compose_group_lists_services(self):
        rows = [e for e in self._p().scan(_v("compose:demo")) if e.name != ".."]
        assert sorted(e.name for e in rows) == ["db", "web"]
        assert _v("compose:demo", "service:web") in {e.loc for e in rows}

    def test_service_lists_its_containers(self):
        rows = [e for e in self._p().scan(_v("compose:demo", "service:web")) if e.name != ".."]
        assert [e.name for e in rows] == ["web"]
        assert rows[0].loc == _v("compose:demo", "service:web", "container:web")

    def test_service_parent_is_compose_group(self):
        rows = self._p().scan(_v("compose:demo", "service:web"))
        assert rows[0].name == ".." and rows[0].loc == _v("compose:demo")


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


def _cfs(*path, endpoint=""):
    # a container under the Containers section, optional FS path
    return VfsPath(scheme="docker", root=endpoint,
                   parts=("containers", "container:web", *path))


class TestScanFilesystem:
    def _p(self, ls_out=_LS, running=_RUNNING):
        return _provider({
            ("inspect", "-f", "{{.State.Running}}"): running,
            ("exec",): ls_out,
        })

    def test_parses_dir_file_symlink(self):
        by = {e.name: e for e in self._p().scan(_cfs("etc", "nginx")) if e.name != ".."}
        assert by["conf.d"].is_dir
        assert by["nginx.conf"].size == 120
        assert by["link"].is_symlink
        assert "a b.txt" in by

    def test_parent_of_fs_subdir(self):
        rows = self._p().scan(_cfs("etc"))
        assert rows[0].name == ".." and rows[0].loc == _cfs()

    def test_parent_of_container_root_is_section(self):
        rows = self._p().scan(_cfs())
        assert rows[0].name == ".." and rows[0].loc == \
            VfsPath(scheme="docker", root="", parts=("containers",))

    def test_stopped_container_raises(self):
        with pytest.raises(OSError):
            self._p(running=_STOPPED).scan(_cfs())


# ---- file ops -------------------------------------------------------------

class TestFileOps:
    def test_open_read_uses_exec_cat(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or b"bytes"
        assert p.open_read(_cfs("etc", "x")).read() == b"bytes"
        assert seen["args"] == ["exec", "web", "cat", "--", "/etc/x"]

    def test_mkdir_uses_exec_mkdir_p(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or b""
        p.mkdir(_cfs("etc"), "new")
        assert seen["args"] == ["exec", "web", "mkdir", "-p", "/etc/new"]

    def test_delete_runs_rm_rf_per_target(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        p.delete([_cfs("a"), _cfs("b")])
        assert calls == [["exec", "web", "rm", "-rf", "--", "/a"],
                         ["exec", "web", "rm", "-rf", "--", "/b"]]

    def test_delete_routes_by_target_kind(self):
        # F8/delete dispatches per entry kind — a volume must NOT become
        # `exec <empty> rm` (the reported "invalid container name" bug).
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        p.delete([_v("volumes", "volume:data")])
        assert calls == [["volume", "rm", "data"]]
        calls.clear()
        p.delete([_v("images", "image:aa")])
        assert calls == [["rmi", "aa"]]
        calls.clear()
        p.delete([_v("networks", "network:n1")])
        assert calls == [["network", "rm", "n1"]]
        calls.clear()
        # The container entry itself (FS root) → remove the container.
        p.delete([_cfs()])
        assert calls == [["rm", "-f", "web"]]

    def test_delete_file_inside_volume_uses_helper(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        p.delete([_v("volumes", "volume:data", "logs")])
        assert calls == [["run", "--rm", "-v", "data:/mnt", p._HELPER,
                          "rm", "-rf", "--", "/mnt/logs"]]

    def test_delete_records_error_not_crash(self):
        # A failing docker rm (e.g. volume in use) surfaces as an OpError.
        def boom(args, *, endpoint="", input=None):
            raise OSError("volume is in use")
        p = DockerProvider()
        p._run = boom
        res = p.delete([_v("volumes", "volume:data")])
        assert res.errors and "in use" in res.errors[0].reason

    def test_is_dir_navigational_nodes_true(self):
        p = self_p = _provider({("inspect", "-f", "{{.State.Running}}"): _RUNNING})
        assert p.is_dir(_v()) is True
        assert p.is_dir(_v("containers")) is True
        assert p.is_dir(_v("compose:demo")) is True
        assert p.is_dir(_v("images", "image:aa")) is False
        assert p.is_dir(_v("networks", "network:n")) is False
        assert p.is_dir(_v("volumes", "volume:data")) is True


class TestWrite:
    def test_open_write_packs_tar_and_cp(self):
        seen = {}
        p = DockerProvider()
        def fr(args, *, endpoint="", input=None):
            seen.update(args=args, input=input); return b""
        p._run = fr
        w = p.open_write(_cfs("etc", "new.txt"), overwrite=True)
        w.write(b"hello"); w.close()
        assert seen["args"] == ["cp", "-", "web:/etc"]
        import io as _io, tarfile as _tar
        tf = _tar.open(fileobj=_io.BytesIO(seen["input"]))
        assert tf.getnames() == ["new.txt"]


class TestWriteAndResolve:
    def test_resolve_empty_spec_is_index(self):
        loc = DockerProvider().resolve_target("", base=VfsPath.local("/"))
        assert loc == _index()

    def test_resolve_remote_index_no_running_check(self):
        loc = DockerProvider().resolve_target("ssh://u@h", base=VfsPath.local("/"))
        assert loc == _index("ssh://u@h")

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
        by = {e.name: e for e in p.scan(_v("containers"))}
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

    def test_scan_compose_sets_compose_flag(self):
        # Verifies the ps-labels → docker.compose pipeline: a ps record with
        # compose labels must surface as docker.compose=="1" on the GROUP entry.
        # _container_entry does NOT set this flag; only _scan_top does (on the
        # group node), so we assert at scan(_v()), not at a deeper level.
        ps = (b'{"Names":"web","State":"running",'
              b'"Labels":"com.docker.compose.project=proj1,com.docker.compose.service=web"}\n')
        p = _provider({("ps", "-a"): ps})
        by = {e.name: e for e in p.scan(_v())}
        assert by["proj1"].extra.get("docker.compose") == "1"

    def test_compose_flag_from_ps_labels_no_subprocess_in_applies_to(self):
        rebuild = {a.id: a for a in DockerProvider().actions()}["docker.rebuild"]
        compose_entry = FileEntry(
            loc=_v("compose:demo", "service:web", "container:web"),
            name="web", size=0, mtime=0.0, is_dir=True,
            extra={"docker.compose": "1", "docker.state": "running"},
        )
        standalone_entry = FileEntry(
            loc=_cfs(), name="lonely", size=0, mtime=0.0, is_dir=True,
            extra={"docker.state": "running"},
        )
        assert rebuild.applies_to(compose_entry) is True
        assert rebuild.applies_to(standalone_entry) is False


class TestActionsV2:
    def test_run_start_resolves_container_name(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append((args, endpoint)) or b""
        act = {a.id: a for a in p.actions()}["docker.start"]
        act.run([_cfs()])  # ("containers","container:web")
        assert calls == [(["start", "web"], "")]

    def test_run_start_on_remote_passes_endpoint(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append((args, endpoint)) or b""
        act = {a.id: a for a in p.actions()}["docker.start"]
        remote_loc = _cfs(endpoint="ssh://h")
        act.run([remote_loc])
        assert calls == [(["start", "web"], "ssh://h")]

    def test_start_applies_to_stateful_entry(self):
        acts = {a.id: a for a in DockerProvider().actions()}
        stopped = FileEntry(loc=_cfs(), name="web", size=0, mtime=0.0, is_dir=True,
                            extra={"docker.state": "exited"})
        assert acts["docker.start"].applies_to(stopped) is True


class TestResolveV2:
    def test_empty_is_top(self):
        assert DockerProvider().resolve_target("", base=VfsPath.local("/")) == _v()

    def test_section_keyword(self):
        assert DockerProvider().resolve_target("images", base=VfsPath.local("/")) == _v("images")

    def test_standalone_container_name(self):
        ps = b'{"Names":"web","State":"running","Labels":"foo=bar"}\n'
        p = _provider({("ps", "-a"): ps,
                       ("inspect", "-f", "{{.State.Running}}"): _RUNNING})
        assert p.resolve_target("web", base=VfsPath.local("/")) == \
            _v("containers", "container:web")

    def test_compose_container_name(self):
        ps = (b'{"Names":"web","State":"running",'
              b'"Labels":"com.docker.compose.project=demo,com.docker.compose.service=web"}\n')
        p = _provider({("ps", "-a"): ps,
                       ("inspect", "-f", "{{.State.Running}}"): _RUNNING})
        assert p.resolve_target("web", base=VfsPath.local("/")) == \
            _v("compose:demo", "service:web", "container:web")

    def test_unknown_raises(self):
        p = _provider({("ps", "-a"): b""})
        with pytest.raises(OSError):
            p.resolve_target("ghost", base=VfsPath.local("/"))

    def test_stopped_container_raises(self):
        ps = b'{"Names":"db","State":"exited","Labels":""}\n'
        p = _provider({("ps", "-a"): ps,
                       ("inspect", "-f", "{{.State.Running}}"): _STOPPED})
        with pytest.raises(OSError, match="not running"):
            p.resolve_target("db", base=VfsPath.local("/"))

    def test_remote_standalone_container_name(self):
        ps = b'{"Names":"web","State":"running","Labels":"foo=bar"}\n'
        p = _provider({("ps", "-a"): ps,
                       ("inspect", "-f", "{{.State.Running}}"): _RUNNING})
        assert p.resolve_target("ssh://u@h/web", base=VfsPath.local("/")) == \
            _v("containers", "container:web", endpoint="ssh://u@h")


class TestColumnsV2:
    def test_container_listing_columns(self):
        # The standalone "S" state column was merged into the Actions cluster.
        labels = [c.label for c in DockerProvider().columns(_v("containers"))]
        assert labels == ["Image", "Status"]

    def test_service_listing_columns_have_no_state_column(self):
        labels = [c.label for c in DockerProvider().columns(_v("compose:d", "service:w"))]
        assert labels == ["Image", "Status"]
        assert "S" not in labels

    def test_images_columns(self):
        assert [c.label for c in DockerProvider().columns(_v("images"))] == ["Size", "Created"]

    def test_networks_columns(self):
        assert [c.label for c in DockerProvider().columns(_v("networks"))] == ["Driver", "Scope"]

    def test_volumes_columns(self):
        cols = DockerProvider().columns(_v("volumes"))
        assert [c.label for c in cols] == ["Size", "Driver", "Used by"]
        # "Used by" is free text → left-aligned; the others keep the default.
        by_key = {c.key: c.align for c in cols}
        assert by_key["docker.usedby"] == "left"
        assert by_key["docker.size"] == "center"

    def test_top_and_fs_have_no_columns(self):
        assert DockerProvider().columns(_v()) == []
        assert DockerProvider().columns(_cfs("etc")) == []

    def test_state_default_sort_orders_running_first(self):
        # The state sort moved from the (removed) "S" column to default_sort.
        p = DockerProvider()
        sort_id, sort_key, descending = p.default_sort(_v("containers"))
        assert sort_id == "docker.state" and descending is False
        run = FileEntry(loc=_cfs(), name="a", size=0, mtime=0.0, is_dir=True,
                        extra={"docker.state": "running", "glyph": "▶"})
        dead = FileEntry(loc=_cfs(), name="b", size=0, mtime=0.0, is_dir=True,
                         extra={"docker.state": "exited", "glyph": "■"})
        assert sort_key(run) < sort_key(dead)

    def test_default_sort_only_for_container_listings(self):
        p = DockerProvider()
        assert p.default_sort(_v("compose:d", "service:w"))[0] == "docker.state"
        assert p.default_sort(_v("images")) is None
        assert p.default_sort(_v()) is None
        assert p.default_sort(_cfs("etc")) is None

    def test_docker_satisfies_provider_columns(self):
        from dunders.core.vfs.provider import ProviderColumns
        assert isinstance(DockerProvider(), ProviderColumns)


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


class TestScanSections:
    def test_images(self):
        j = (b'{"ID":"aa","Repository":"nginx","Tag":"1.27","Size":"200MB","CreatedSince":"2 days ago"}\n'
             b'{"ID":"bb","Repository":"<none>","Tag":"<none>","Size":"5MB","CreatedSince":"1 week ago"}\n')
        p = _provider({("images",): j})
        rows = [e for e in p.scan(_v("images")) if e.name != ".."]
        assert rows[0].name == "nginx:1.27" and rows[0].is_dir is False
        assert rows[0].loc == _v("images", "image:aa")
        assert rows[0].extra["docker.size"] == "200MB"
        assert rows[1].name == "bb"  # <none> falls back to id

    def test_networks(self):
        j = b'{"ID":"n1","Name":"bridge","Driver":"bridge","Scope":"local"}\n'
        p = _provider({("network", "ls"): j})
        rows = [e for e in p.scan(_v("networks")) if e.name != ".."]
        assert rows[0].name == "bridge" and rows[0].is_dir is False
        assert rows[0].loc == _v("networks", "network:n1")
        assert rows[0].extra["docker.driver"] == "bridge"

    def test_volumes(self):
        # Size comes from the `system df -v` disk-usage scan (a single JSON array);
        # "Used by" comes from the ps -aq + inspect container→volume map.
        j = (b'[{"Name":"data","Driver":"local","Size":"200MB",'
             b'"Mountpoint":"/var/lib/docker/volumes/data/_data"}]')
        inspect = (b'[{"Name":"/web","Config":{"Image":"nginx:latest"},'
                   b'"Mounts":[{"Name":"data"}]}]')
        p = _provider({("system", "df", "-v"): j,
                       ("ps", "-a", "-q"): b"c1\n",
                       ("inspect",): inspect})
        rows = [e for e in p.scan(_v("volumes")) if e.name != ".."]
        assert rows[0].name == "data" and rows[0].is_dir is True
        assert rows[0].loc == _v("volumes", "volume:data")
        assert rows[0].extra["docker.mountpoint"].endswith("_data")
        assert rows[0].extra["docker.size"] == "200MB"
        assert rows[0].extra["docker.usedby"] == "web"

    def test_volume_usedby_multiple_and_dangling(self):
        # Two containers → "first +1"; a volume no container mounts → blank.
        j = (b'[{"Name":"shared","Driver":"local","Size":"0B","Mountpoint":"/m/s"},'
             b'{"Name":"orphan","Driver":"local","Size":"0B","Mountpoint":"/m/o"}]')
        inspect = (b'[{"Name":"/web","Config":{"Image":"nginx"},'
                   b'"Mounts":[{"Name":"shared"}]},'
                   b'{"Name":"/api","Config":{"Image":"go"},'
                   b'"Mounts":[{"Name":"shared"}]}]')
        p = _provider({("system", "df", "-v"): j,
                       ("ps", "-a", "-q"): b"c1\nc2\n",
                       ("inspect",): inspect})
        by = {e.name: e.extra["docker.usedby"]
              for e in p.scan(_v("volumes")) if e.name != ".."}
        assert by["shared"] == "web +1"
        assert by["orphan"] == ""

    def test_container_volume_map_resilient(self):
        # No containers → no inspect call, empty map. _run failure → empty map.
        p = _provider({("ps", "-a", "-q"): b"\n"})
        assert p._container_volume_map("") == {}
        p2 = _provider({})  # any _run raises AssertionError → caught → {}
        assert p2._container_volume_map("") == {}

    def test_volumes_fallback_to_volume_ls_without_size(self):
        # When `system df` is unavailable, fall back to `volume ls` (blank Size).
        j = b'{"Name":"data","Driver":"local","Mountpoint":"/var/lib/docker/volumes/data/_data"}\n'
        p = _provider({("volume", "ls"): j})  # no `system df` key → _run raises
        rows = [e for e in p.scan(_v("volumes")) if e.name != ".."]
        assert rows[0].name == "data"
        assert rows[0].extra["docker.size"] == ""
        assert rows[0].extra["docker.mountpoint"].endswith("_data")

    def test_volume_size_sorts_by_bytes_not_lexically(self):
        # The Size column's sort_key parses human sizes to bytes, so "1.2GB"
        # outranks "900MB" (a lexical sort would put "1.2GB" first ascending).
        cols = {c.key: c for c in DockerProvider().columns(_v("volumes"))}
        key = cols["docker.size"].sort_key

        def _vol(name, size):
            return FileEntry(loc=_v("volumes", "volume:" + name), name=name,
                             size=0, mtime=0.0, is_dir=True,
                             extra={"docker.size": size})

        assert key(_vol("big", "1.2GB")) > key(_vol("small", "900MB"))
        # Parser handles binary units and blanks.
        assert DockerProvider._parse_size("1KiB") == 1024
        assert DockerProvider._parse_size("0B") == 0.0
        assert DockerProvider._parse_size("") == 0.0
        assert DockerProvider._parse_size("N/A") == 0.0


class TestSectionActions:
    def _acts(self, p=None):
        return {a.id: a for a in (p or DockerProvider()).actions()}

    def test_image_remove_argv(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        self._acts(p)["docker.image.remove"].run([_v("images", "image:aa")])
        assert calls == [["rmi", "aa"]]

    def test_network_and_volume_remove_argv(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        self._acts(p)["docker.network.remove"].run([_v("networks", "network:n1")])
        self._acts(p)["docker.volume.remove"].run([_v("volumes", "volume:data")])
        assert calls == [["network", "rm", "n1"], ["volume", "rm", "data"]]

    def test_compose_up_down_argv(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        self._acts(p)["docker.compose.up"].run([_v("compose:demo")])
        self._acts(p)["docker.compose.down"].run([_v("compose:demo")])
        assert calls == [["compose", "-p", "demo", "up", "-d"],
                         ["compose", "-p", "demo", "down"]]

    def test_applies_to_gating(self):
        a = self._acts()
        img = FileEntry(loc=_v("images", "image:aa"), name="x", size=0, mtime=0.0, is_dir=False)
        comp = FileEntry(loc=_v("compose:demo"), name="demo", size=0, mtime=0.0, is_dir=True)
        assert a["docker.image.remove"].applies_to(img) is True
        assert a["docker.image.remove"].applies_to(comp) is False
        assert a["docker.compose.up"].applies_to(comp) is True
        assert a["docker.compose.up"].applies_to(img) is False


class TestPrune:
    def test_prune_argv_by_section(self):
        p = DockerProvider()
        assert p.prune_argv(_v("images", "image:aa")) == ["image", "prune", "-f"]
        assert p.prune_argv(_v("volumes", "volume:d")) == ["volume", "prune", "-f"]
        assert p.prune_argv(_v("networks", "network:n")) == ["network", "prune", "-f"]
        assert p.prune_argv(_v("images")) == ["image", "prune", "-f"]
        assert p.prune_argv(_v()) == ["system", "prune", "-f"]

    def test_prune_action_runs(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append(args) or b""
        act = {a.id: a for a in p.actions()}["docker.prune"]
        act.run([_v("images")])
        assert calls == [["image", "prune", "-f"]]


class TestVolumeBrowse:
    def _p(self, ls=_LS):
        return _provider({("run", "--rm"): ls})

    def test_scan_volume_argv_and_parse(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or _LS
        rows = [e for e in p.scan(_v("volumes", "volume:data", "logs")) if e.name != ".."]
        assert seen["args"][:2] == ["run", "--rm"]
        assert "-v" in seen["args"] and "data:/mnt" in seen["args"]
        assert seen["args"][-3:] == ["ls", "-la", "--full-time"] or "ls" in seen["args"]
        assert {e.name for e in rows} >= {"conf.d", "nginx.conf"}

    def test_open_read_volume_argv(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or b"data"
        assert p.open_read(_v("volumes", "volume:data", "a.txt")).read() == b"data"
        assert "cat" in seen["args"] and seen["args"][-1] == "/mnt/a.txt"

    def test_volume_parent_entry(self):
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: _LS
        rows = p.scan(_v("volumes", "volume:data"))
        assert rows[0].name == ".." and rows[0].loc == _v("volumes")


class TestPreview:
    def _entry(self, loc, **extra):
        return FileEntry(loc=loc, name=loc.parts[-1], size=0, mtime=0.0,
                         is_dir=True, extra=extra)

    def test_docker_is_provider_preview(self):
        assert isinstance(DockerProvider(), ProviderPreview)

    def test_container_logs(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or b"log-lines"
        r = p.preview(_cfs(), self._entry(_cfs(), **{"docker.state": "running"}))
        assert isinstance(r, PreviewResult) and r.kind == "log"
        assert seen["args"][:2] == ["logs", "--tail"] and seen["args"][-1] == "web"
        assert r.text == "log-lines"

    def test_compose_logs(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or b"proj-logs"
        loc = _v("compose:demo")
        r = p.preview(loc, self._entry(loc))
        assert r.kind == "log"
        assert seen["args"][:3] == ["compose", "-p", "demo"] and "logs" in seen["args"]

    def test_image_inspect_json(self):
        seen = {}
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: seen.update(args=args) or b"[{}]"
        loc = _v("images", "image:aa")
        r = p.preview(loc, self._entry(loc))
        assert r.kind == "json" and seen["args"] == ["image", "inspect", "aa"]

    def test_network_and_volume_inspect(self):
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: b"[{}]"
        n = _v("networks", "network:n1")
        assert p.preview(n, self._entry(n)).kind == "json"
        v = _v("volumes", "volume:data")
        assert p.preview(v, self._entry(v)).kind == "json"

    def test_volume_preview_leads_with_used_by(self):
        # F3 on a volume prepends a "Used by:" block (container + image + state)
        # from `ps --filter volume=`, then the raw `volume inspect` JSON.
        def fake(args, *, endpoint="", input=None):
            if args[:2] == ["ps", "-a"]:
                return b"web\tnginx:latest\trunning\n"
            if args[:2] == ["volume", "inspect"]:
                return b'[{"Name":"data","Mountpoint":"/m/d"}]'
            raise AssertionError(args)
        p = DockerProvider()
        p._run = fake
        v = _v("volumes", "volume:data")
        r = p.preview(v, self._entry(v))
        assert r.kind == "json"
        assert "Used by:" in r.text
        assert "web  (nginx:latest)  [running]" in r.text
        # The inspect JSON still follows the header.
        assert '"Mountpoint": "/m/d"' in r.text or '"Mountpoint":"/m/d"' in r.text

    def test_volume_preview_dangling_shows_none(self):
        def fake(args, *, endpoint="", input=None):
            if args[:2] == ["ps", "-a"]:
                return b""  # no container references it
            if args[:2] == ["volume", "inspect"]:
                return b"[{}]"
            raise AssertionError(args)
        p = DockerProvider()
        p._run = fake
        v = _v("volumes", "volume:orphan")
        r = p.preview(v, self._entry(v))
        assert "(none" in r.text and "Used by:" in r.text

    def test_fs_file_returns_none(self):
        p = DockerProvider()
        assert p.preview(_cfs("etc", "x"), self._entry(_cfs("etc", "x"))) is None


def test_pull_argv():
    calls = []
    p = DockerProvider()
    p._run = lambda args, *, endpoint="", input=None: calls.append((args, endpoint)) or b""
    p.pull("nginx:1.27", endpoint="ssh://h")
    assert calls == [(["pull", "nginx:1.27"], "ssh://h")]


class TestRunImage:
    def test_run_argv_minimal(self):
        assert DockerProvider().run_argv({"image": "nginx"}) == ["run", "-d", "nginx"]

    def test_run_argv_full(self):
        argv = DockerProvider().run_argv({
            "image": "nginx", "name": "web",
            "ports": ["8080:80"], "volumes": ["/data:/var/www"], "detach": True})
        assert argv == ["run", "-d", "--name", "web",
                        "-p", "8080:80", "-v", "/data:/var/www", "nginx"]

    def test_run_image_issues_argv(self):
        calls = []
        p = DockerProvider()
        p._run = lambda args, *, endpoint="", input=None: calls.append((args, endpoint)) or b""
        p.run_image({"image": "redis"}, endpoint="ssh://h")
        assert calls == [(["run", "-d", "redis"], "ssh://h")]

    def test_run_action_sentinel_in_actions(self):
        ids = {a.id for a in DockerProvider().actions()}
        assert "docker.run" in ids

    def test_run_action_applies_to_image_entries(self):
        acts = {a.id: a for a in DockerProvider().actions()}
        img = FileEntry(loc=_v("images", "image:aa"), name="nginx", size=0, mtime=0.0,
                        is_dir=False, extra={})
        ctr = FileEntry(loc=_cfs(), name="web", size=0, mtime=0.0, is_dir=True,
                        extra={"docker.state": "running"})
        assert acts["docker.run"].applies_to(img) is True
        assert acts["docker.run"].applies_to(ctr) is False


class TestActionIcons:
    def test_all_action_icons_are_single_cell(self):
        # Regression guard: every docker action icon must be exactly 1 terminal
        # cell wide so no icon causes the action strip to overflow the panel edge
        # (a 2-cell emoji like 🧹 doubled the right border).
        # Revert-proof: cell_len('🧹') == 2, so this test FAILS on the old icon.
        from rich.cells import cell_len
        for a in DockerProvider().actions():
            assert cell_len(a.icon or "") == 1, (
                f"{a.id} icon {a.icon!r} is {cell_len(a.icon or '')} cells wide "
                "(must be exactly 1)"
            )
