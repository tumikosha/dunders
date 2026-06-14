"""FtpProvider — spec parsing (no server) + integration against a local
pyftpdlib FTP server.
"""

import ftplib
import socket
import threading

import pytest

from dunders.core.vfs import VfsPath
from dunders.fm.providers.ftp_provider import (
    FtpProvider,
    _canonical_root,
    _connect_error,
    _parse_list_line,
    _parse_spec,
)
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry

try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    _HAS_PYFTPDLIB = True
except ImportError:
    _HAS_PYFTPDLIB = False

_needs_server = pytest.mark.skipif(not _HAS_PYFTPDLIB, reason="pyftpdlib not installed")


async def _wait_open(pilot, panel, *, scheme="ftp", tries=40):
    """Pump the event loop until the panel has connected (cwd_loc on `scheme`)
    and finished its async listing."""
    for _ in range(tries):
        await pilot.pause()
        if panel.cwd_loc.scheme == scheme and not panel._loading:
            return

# pyftpdlib's serve_forever thread errors in its kqueue poll when the test
# closes the server out from under it — a harmless teardown artifact.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)


# ---- spec parsing (no server) --------------------------------------------

class TestParseSpec:
    def test_user_pass_host_port_path(self):
        assert _parse_spec("bob:pw@host.example:2121/pub/files") == (
            "host.example", 2121, "bob", "pw", "pub/files"
        )

    def test_anonymous_host_only(self):
        assert _parse_spec("ftp.example.org") == (
            "ftp.example.org", 21, None, None, ""
        )

    def test_double_slash_prefix_stripped(self):
        # "ftp://host/x" arrives here as "//host/x" after the scheme split.
        assert _parse_spec("//host/x")[0] == "host"
        assert _parse_spec("//host/x")[4] == "x"

    def test_user_without_password(self):
        host, port, user, pw, path = _parse_spec("alice@host/")
        assert (host, user, pw) == ("host", "alice", None)

    def test_canonical_root(self):
        assert _canonical_root("h", 21, "bob") == "bob@h:21"
        assert _canonical_root("h", 21, None) == "h:21"


class TestListFallback:
    """Servers without MLSD (which reply '500 Unknown command') must fall back
    to LIST parsing rather than crashing — the reported bug."""

    def test_parse_list_line_dir_and_file(self):
        assert _parse_list_line(
            "drwxr-xr-x 2 user group 4096 Jun 14 12:00 mydir"
        ) == ("mydir", True, 4096)
        assert _parse_list_line(
            "-rw-r--r-- 1 user group 1234 Jun 14 12:00 file.txt"
        ) == ("file.txt", False, 1234)

    def test_parse_list_line_symlink_strips_target(self):
        name, is_dir, _ = _parse_list_line(
            "lrwxrwxrwx 1 u g 7 Jun 14 12:00 link -> /target"
        )
        assert name == "link"

    def test_parse_list_line_junk_ignored(self):
        assert _parse_list_line("total 8") is None
        assert _parse_list_line("garbage") is None

    def test_raw_list_falls_back_to_list_when_mlsd_unsupported(self):
        class _FakeConn:
            def mlsd(self, remote):
                raise ftplib.error_perm("500 Unknown command.")

            def retrlines(self, cmd, callback):
                callback("drwxr-xr-x 2 u g 4096 Jun 14 12:00 sub")
                callback("-rw-r--r-- 1 u g 5 Jun 14 12:00 a.txt")

        rows = FtpProvider._raw_list(_FakeConn(), "/")
        by = {name: (is_dir, size) for name, is_dir, size, _ in rows}
        assert by == {"sub": (True, 4096), "a.txt": (False, 5)}

    def test_raw_list_returns_empty_when_everything_fails(self):
        class _DeadConn:
            def mlsd(self, remote):
                raise ftplib.error_perm("500")

            def retrlines(self, cmd, callback):
                raise ftplib.error_perm("500")

        assert FtpProvider._raw_list(_DeadConn(), "/") == []


class TestConnectError:
    def test_auth(self):
        msg = _connect_error(ftplib.error_perm("530 Login incorrect."), "h", 21, "bob")
        assert "login failed" in msg.lower() and "bob" in msg

    def test_unknown_host(self):
        assert "Unknown host" in _connect_error(socket.gaierror(), "nope.invalid", 21, "u")

    def test_refused(self):
        assert "refused" in _connect_error(ConnectionRefusedError(), "h", 9999, "u").lower()

    def test_timeout(self):
        assert "timed out" in _connect_error(TimeoutError(), "h", 21, "u").lower()


class TestNeedsPassword:
    def test_user_without_password_needs_prompt(self):
        assert FtpProvider().needs_password("tumi@host:4021/") is True

    def test_inline_password_no_prompt(self):
        assert FtpProvider().needs_password("tumi:pw@host/") is False

    def test_anonymous_no_prompt(self):
        assert FtpProvider().needs_password("ftp.example.org") is False

    def test_no_prompt_when_already_connected_this_session(self):
        p = FtpProvider()
        assert p.needs_password("tumi@host:4021/") is True  # first time
        p._creds[_canonical_root("host", 4021, "tumi")] = ("host", 4021, "tumi", "pw")
        assert p.needs_password("tumi@host:4021/") is False  # reconnect reuses


# ---- integration (requires pyftpdlib) ------------------------------------

@pytest.fixture
def ftp_server(tmp_path):
    root = tmp_path / "ftproot"
    root.mkdir()
    (root / "hello.txt").write_text("hi there")
    (root / "dir").mkdir()
    (root / "dir" / "inner.txt").write_text("inner")

    authorizer = DummyAuthorizer()
    authorizer.add_user("bob", "secret", str(root), perm="elradfmwMT")
    handler = type("_Handler", (FTPHandler,), {"authorizer": authorizer})
    server = FTPServer(("127.0.0.1", 0), handler)
    host, port = server.address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port, root
    finally:
        server.close_all()
        thread.join(timeout=3)


def _root_loc(port) -> VfsPath:
    return VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=())


def _open(provider, port):
    """Connect via resolve_target and return the root locator."""
    return provider.resolve_target(
        f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
    )


@_needs_server
class TestResolveAndScan:
    def test_resolve_target_connects(self, ftp_server):
        _host, port, _root = ftp_server
        loc = FtpProvider().resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        assert loc == _root_loc(port)

    def test_resolve_target_bad_host_raises_informative(self):
        # Connection failure raises a specific reason (None means "can't
        # interpret the spec", which is different).
        with pytest.raises(OSError) as ei:
            FtpProvider().resolve_target("nobody@127.0.0.1:1/", base=VfsPath.local("/"))
        assert "127.0.0.1:1" in str(ei.value)

    def test_resolve_target_empty_spec_returns_none(self):
        # No host → not interpretable → None (not an error).
        assert FtpProvider().resolve_target("", base=VfsPath.local("/")) is None

    def test_resolve_target_invalid_port_raises(self):
        # A port > 65535 otherwise surfaces as a misleading "unknown host".
        with pytest.raises(OSError) as ei:
            FtpProvider().resolve_target("u@host:4021111/", base=VfsPath.local("/"))
        assert "port" in str(ei.value).lower() and "4021111" in str(ei.value)

    def test_injected_password_connects(self, ftp_server):
        _host, port, _root = ftp_server
        # spec gives the user but no password; the prompted password is supplied
        # via the password= argument (what the app passes after the prompt).
        loc = FtpProvider().resolve_target(
            f"bob@127.0.0.1:{port}/", base=VfsPath.local("/"), password="secret"
        )
        assert loc == _root_loc(port)

    def test_wrong_injected_password_raises_login_failed(self, ftp_server):
        _host, port, _root = ftp_server
        with pytest.raises(OSError) as ei:
            FtpProvider().resolve_target(
                f"bob@127.0.0.1:{port}/", base=VfsPath.local("/"), password="wrong"
            )
        assert "login failed" in str(ei.value).lower()

    def test_scan_lists_root(self, ftp_server):
        _host, port, _root = ftp_server
        p = FtpProvider()
        loc = _open(p, port)
        names = {e.name for e in p.scan(loc, include_parent=False)}
        assert names == {"hello.txt", "dir"}
        by = {e.name: e for e in p.scan(loc, include_parent=False)}
        assert by["dir"].is_dir and not by["hello.txt"].is_dir
        assert by["hello.txt"].size == len("hi there")

    def test_descend_and_is_dir(self, ftp_server):
        _host, port, _root = ftp_server
        p = FtpProvider()
        _open(p, port)
        dir_loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("dir",))
        assert p.is_dir(dir_loc)
        assert {e.name for e in p.scan(dir_loc, include_parent=False)} == {"inner.txt"}


@_needs_server
class TestTransfer:
    def test_read_member(self, ftp_server):
        _host, port, _root = ftp_server
        p = FtpProvider()
        _open(p, port)
        loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        with p.open_read(loc) as fh:
            assert fh.read() == b"hi there"

    def test_download_to_local(self, ftp_server, tmp_path):
        _host, port, _root = ftp_server
        reg = default_registry()
        reg.for_scheme("ftp").resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        src = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        dest = tmp_path / "out"
        dest.mkdir()
        res = transfer(reg, [src], VfsPath.local(dest), mode="copy")
        assert not res.errors
        assert (dest / "hello.txt").read_bytes() == b"hi there"

    def test_upload_from_local(self, ftp_server, tmp_path):
        _host, port, root = ftp_server
        reg = default_registry()
        reg.for_scheme("ftp").resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        local = tmp_path / "up.txt"
        local.write_text("uploaded")
        res = transfer(reg, [VfsPath.local(local)], _root_loc(port), mode="copy")
        assert not res.errors
        assert (root / "up.txt").read_text() == "uploaded"


@_needs_server
class TestMutations:
    def test_mkdir(self, ftp_server):
        _host, port, root = ftp_server
        p = FtpProvider()
        _open(p, port)
        p.mkdir(_root_loc(port), "newdir")
        assert (root / "newdir").is_dir()

    def test_delete_file(self, ftp_server):
        _host, port, root = ftp_server
        p = FtpProvider()
        _open(p, port)
        loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        res = p.delete([loc])
        assert not res.errors
        assert not (root / "hello.txt").exists()

    def test_delete_directory_recursive(self, ftp_server):
        _host, port, root = ftp_server
        p = FtpProvider()
        _open(p, port)
        loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("dir",))
        res = p.delete([loc])
        assert not res.errors
        assert not (root / "dir").exists()

    def test_open_write_refuses_existing_without_overwrite(self, ftp_server):
        _host, port, _root = ftp_server
        p = FtpProvider()
        _open(p, port)
        loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        with pytest.raises(OSError):
            p.open_write(loc)


def test_registered_in_default_registry():
    assert "ftp" in default_registry().schemes()


def test_panel_does_not_crash_when_provider_scan_raises(tmp_path):
    """Defense in depth: a provider scan that raises degrades to an empty
    listing instead of taking down the TUI (the FTP/MLSD crash class)."""
    from dunders.core.vfs import VfsRegistry
    from dunders.fm.file_panel import FilePanel

    class _Boom:
        scheme = "boom"
        capabilities = frozenset({"read"})

        def scan(self, loc, *, show_hidden=False, include_parent=True):
            raise RuntimeError("network down")

    reg = VfsRegistry()
    reg.register(_Boom())
    panel = FilePanel(cwd=tmp_path, registry=reg)
    panel.cwd_loc = VfsPath(scheme="boom", root="x", parts=())
    panel.refresh_listing()  # must not raise
    assert list(panel.entries) == []


@_needs_server
@pytest.mark.asyncio
async def test_open_ftp_via_dunders_menu_navigates_panel(ftp_server, tmp_path):
    from dunders.app import DundersApp

    _host, port, _root = ftp_server
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app._active_panel()
        # "_" menu → FTP → type the connection string → opens & lists it.
        # Both connect and listing are async now, so wait for them to land.
        app._do_open_dunder("ftp", f"bob:secret@127.0.0.1:{port}/")
        await _wait_open(pilot, panel)
        names = {e.name for e in panel.entries if not e.is_parent}
        assert names == {"hello.txt", "dir"}


@_needs_server
@pytest.mark.asyncio
async def test_open_ftp_without_password_prompts_then_connects(ftp_server, tmp_path):
    from dunders.app import DundersApp
    from dunders.fm.dialogs import InputDialog

    _host, port, _root = ftp_server
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        panel = app._active_panel()
        # User given but no inline password → a masked password dialog opens
        # instead of navigating or failing.
        app._do_open_dunder("ftp", f"bob@127.0.0.1:{port}/")
        await pilot.pause()
        dialog = app.query_one(InputDialog)
        assert dialog._input.password is True  # masked
        assert panel.cwd_loc.scheme == "file"  # not navigated yet
        # Enter the password → connects and lists the remote.
        dialog._input.value = "secret"
        dialog.action_submit()
        await _wait_open(pilot, panel)
        assert panel.cwd_loc.scheme == "ftp"
        assert {e.name for e in panel.entries if not e.is_parent} == {"hello.txt", "dir"}


@_needs_server
def test_connection_password_returns_login_secret(ftp_server):
    _host, port, _root = ftp_server
    p = FtpProvider()
    root = f"bob@127.0.0.1:{port}"
    p.resolve_target(f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/"))
    assert p.connection_password(root) == "secret"
    assert p.connection_password("unknown@nope:21") is None
