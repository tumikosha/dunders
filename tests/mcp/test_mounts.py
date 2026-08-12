import json
import pytest
from dunders.core.vfs import VfsRegistry
from dunders.mcp.mounts import MountTable
from dunders.mcp import errors as e


class FakeProvider:
    """A provider that records resolve_target and answers is_dir/scan trivially."""
    def __init__(self, scheme, *, slow=False):
        self.scheme = scheme
        self.capabilities = frozenset({"read", "write"} | ({"slow"} if slow else set()))
        self.resolved = []
    def resolve_target(self, spec, *, base, password=None):
        self.resolved.append((spec, password))
        return base
    def scan(self, loc, *, show_hidden=False, include_parent=True):
        return []
    def is_dir(self, loc):
        return True


def _registry():
    reg = VfsRegistry()
    reg.register(FakeProvider("file"))
    reg.register(FakeProvider("sftp", slow=True))
    return reg


def _write(p, items):
    p.write_text(json.dumps({"bookmarks": items}), encoding="utf-8")


def test_mounts_built_from_file(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [
        {"label": "home", "uri": "file:///home/me"},
        {"label": "prod", "uri": "sftp://me@host!/srv", "password": "pw"},
    ])
    table = MountTable(_registry(), path=f)
    labels = {m.label for m in table.mounts()}
    assert labels == {"home", "prod"}


def test_mcp_false_bookmark_skipped(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [
        {"label": "shown", "uri": "file:///a"},
        {"label": "hidden", "uri": "file:///b", "mcp": False},
    ])
    assert {m.label for m in MountTable(_registry(), path=f).mounts()} == {"shown"}


def test_allow_narrows(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///a"}, {"label": "b", "uri": "file:///b"}])
    table = MountTable(_registry(), path=f, allow={"a"})
    assert {m.label for m in table.mounts()} == {"a"}


def test_get_unknown_raises_mount_not_found(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "file:///a"}])
    table = MountTable(_registry(), path=f)
    with pytest.raises(e.McpError) as ei:
        table.get("nope")
    assert ei.value.code == e.MOUNT_NOT_FOUND


def test_resolve_descends_clean_path(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    loc = table.resolve("prod", "app/config")
    assert loc.scheme == "sftp" and loc.parts == ("srv", "app", "config")


def test_resolve_traversal_rejected(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    for bad in ["../etc", "a/../../b", "/abs"]:
        with pytest.raises(e.McpError) as ei:
            table.resolve("prod", bad)
        assert ei.value.code == e.ACCESS_DENIED


def test_resolve_backslash_traversal_rejected(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    for bad in ["..\\etc", "a\\..\\b", "..\\..\\etc\\passwd"]:
        with pytest.raises(e.McpError) as ei:
            table.resolve("prod", bad)
        assert ei.value.code == e.ACCESS_DENIED


def test_empty_path_is_mount_root(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    assert table.resolve("prod", "").parts == ("srv",)


def test_lazy_connect_calls_resolve_target_once(tmp_path):
    f = tmp_path / "bm.json"
    _write(f, [{"label": "prod", "uri": "sftp://me@host!/srv", "password": "pw"}])
    reg = _registry()
    table = MountTable(reg, path=f)
    m = table.get("prod")
    assert not table.connected("prod")
    table.ensure_connected(m)
    table.ensure_connected(m)  # idempotent
    prov = reg.resolve(m.loc)
    assert prov.resolved == [("me@host/srv", "pw")]  # connected exactly once
    assert table.connected("prod")


def test_reload_on_mtime_change_adds_and_drops(tmp_path):
    import os
    f = tmp_path / "bm.json"
    _write(f, [{"label": "a", "uri": "sftp://me@host!/srv"}])
    reg = _registry()
    table = MountTable(reg, path=f)
    table.ensure_connected(table.get("a"))
    assert table.connected("a")
    # rewrite: drop 'a', add 'b'; bump mtime
    _write(f, [{"label": "b", "uri": "file:///b"}])
    m = f.stat().st_mtime
    os.utime(f, (m + 10, m + 10))
    assert {mt.label for mt in table.mounts()} == {"b"}
    assert not table.connected("a")  # vanished label's connect flag dropped


def test_noai_file_mount_hidden(tmp_path):
    (tmp_path / ".dunders-noai").write_text("")
    secret = tmp_path / "secret"
    secret.mkdir()
    f = tmp_path / "bm.json"
    _write(f, [{"label": "secret", "uri": secret.as_uri()},
               {"label": "ok", "uri": "sftp://me@host!/srv"}])
    table = MountTable(_registry(), path=f)
    # local mount under a .dunders-noai marker is excluded; remote stays
    assert {m.label for m in table.mounts()} == {"ok"}
