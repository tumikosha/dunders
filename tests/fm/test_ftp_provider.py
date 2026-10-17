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


class _VsftpdConn:
    """Fake vsftpd: no MLSD, plain LIST hides dotfiles, RMD refuses a
    non-empty directory with 550."""

    def __init__(self, tree: dict[str, list[str]]) -> None:
        self.tree = {d: list(names) for d, names in tree.items()}

    def mlsd(self, remote):
        raise ftplib.error_perm("500 Unknown command.")

    def retrlines(self, cmd, callback):
        show_all = cmd.startswith("LIST -a ")
        path = cmd.split(" ")[-1]
        if show_all:
            callback("drwxr-xr-x 2 u g 4096 Jun 14 12:00 .")
            callback("drwxr-xr-x 2 u g 4096 Jun 14 12:00 ..")
        for name in self.tree.get(path, []):
            if show_all or not name.startswith("."):
                callback(f"-rw-r--r-- 1 u g 5 Jun 14 12:00 {name}")

    def delete(self, remote):
        parent, _, name = remote.rpartition("/")
        if remote in self.tree or name not in self.tree.get(parent, []):
            raise ftplib.error_perm("550 Delete operation failed.")
        self.tree[parent].remove(name)

    def rmd(self, remote):
        if self.tree.get(remote):
            raise ftplib.error_perm("550 Remove directory operation failed.")
        self.tree.pop(remote, None)


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

    def test_raw_list_shows_dotfiles_on_vsftpd(self):
        """vsftpd has no MLSD and hides dotfiles from a plain LIST; only
        ``LIST -a`` returns them. Without them a recursive delete left
        ``.DS_Store`` behind and RMD failed with 550."""
        rows = FtpProvider._raw_list(_VsftpdConn({"/d": [".DS_Store", "a.txt"]}), "/d")
        assert {r[0] for r in rows} >= {".DS_Store", "a.txt"}

    def test_raw_list_plain_list_when_dash_a_rejected(self):
        class _NoOptsConn:
            def mlsd(self, remote):
                raise ftplib.error_perm("500 Unknown command.")

            def retrlines(self, cmd, callback):
                if " -a " in cmd:
                    raise ftplib.error_perm("550 No such file: -a")
                callback("-rw-r--r-- 1 u g 5 Jun 14 12:00 a.txt")

        rows = FtpProvider._raw_list(_NoOptsConn(), "/")
        assert [r[0] for r in rows] == ["a.txt"]

    def test_delete_dir_with_hidden_file_on_vsftpd(self):
        conn = _VsftpdConn({"/d": [".DS_Store", "a.txt"]})
        FtpProvider()._delete_one(conn, "/d")
        assert conn.tree == {}

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

    def test_upload_streams_so_written_tracks_the_server(self, ftp_server, tmp_path):
        """Each chunk must reach the server as it is written. The old writer
        buffered the whole file and uploaded in close, so the bar said 100%
        while the server had nothing yet, then the dialog froze."""
        _host, port, root = ftp_server
        reg = default_registry()
        reg.for_scheme("ftp").resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        local = tmp_path / "big.bin"
        data = bytes(range(256)) * (3 * 1024 * 1024 // 256 + 7)  # > 3 chunks
        local.write_bytes(data)
        statuses = []
        res = transfer(reg, [VfsPath.local(local)], _root_loc(port), mode="copy",
                       on_status=statuses.append)
        assert not res.errors
        assert (root / "big.bin").read_bytes() == data
        last = statuses[-1]
        assert last.read_bytes == last.written_bytes == len(data)
        # Written grew alongside reading, not in one jump at the end.
        assert any(0 < s.written_bytes < len(data) for s in statuses)

    def test_download_streams_progress_and_cancels(self, ftp_server, tmp_path):
        """A big download reports bytes as they arrive and honours Cancel
        mid-file. The old open_read fetched the whole file in one call first:
        no progress, and Cancel was ignored until it finished."""
        import threading
        _host, port, root = ftp_server
        data = bytes(range(256)) * (4 * 1024 * 1024 // 256)  # 4 MiB
        (root / "big.bin").write_bytes(data)
        reg = default_registry()
        reg.for_scheme("ftp").resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        src = _root_loc(port).child("big.bin")
        statuses = []
        res = transfer(reg, [src], VfsPath.local(tmp_path), mode="copy",
                       on_status=statuses.append)
        assert not res.errors
        assert (tmp_path / "big.bin").read_bytes() == data
        assert any(0 < s.read_bytes < len(data) for s in statuses)

        ev = threading.Event()
        out = tmp_path / "cancel"
        out.mkdir()

        def _on_status(st):
            if st.read_bytes >= 1024 * 1024:
                ev.set()  # cancel once the first MiB arrived

        res = transfer(reg, [src], VfsPath.local(out), mode="copy",
                       on_status=_on_status, cancel_event=ev)
        assert res.cancelled
        assert not (out / "big.bin").exists()   # partial removed
        # The shared connection still works after the abandoned download.
        names = [e.name for e in reg.for_scheme("ftp").scan(
            _root_loc(port), include_parent=False)]
        assert "big.bin" in names

    def test_ftp_to_ftp_same_server_does_not_deadlock(self, ftp_server):
        _host, port, root = ftp_server
        reg = default_registry()
        reg.for_scheme("ftp").resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        res = transfer(reg, [_root_loc(port).child("hello.txt")],
                       _root_loc(port).child("dir"), mode="copy")
        assert not res.errors
        assert (root / "dir" / "hello.txt").read_text() == "hi there"

    def test_read_missing_file_raises_oserror(self, ftp_server):
        _host, port, _root = ftp_server
        p = FtpProvider()
        _open(p, port)
        with pytest.raises(OSError):
            p.open_read(_root_loc(port).child("nope.bin"))

    def test_upload_empty_file(self, ftp_server, tmp_path):
        _host, port, root = ftp_server
        reg = default_registry()
        reg.for_scheme("ftp").resolve_target(
            f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
        )
        local = tmp_path / "empty.txt"
        local.write_bytes(b"")
        res = transfer(reg, [VfsPath.local(local)], _root_loc(port), mode="copy")
        assert not res.errors
        assert (root / "empty.txt").read_bytes() == b""


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

    def test_delete_directory_reports_progress_per_entry(self, ftp_server):
        """One big folder used to sit at 0% until it was gone, then jump to
        100%: progress was bumped once per *selected* target. Now every file
        and directory removed moves the bar, after an indeterminate walk."""
        _host, port, root = ftp_server
        big = root / "big"
        (big / "sub").mkdir(parents=True)
        for i in range(5):
            (big / f"f{i}.txt").write_text("x")
        (big / "sub" / ".hidden").write_text("x")
        p = FtpProvider()
        _open(p, port)
        loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("big",))
        calls = []
        res = p.delete([loc], on_progress=lambda i, n: calls.append((i, n)))
        assert not res.errors and res.succeeded == [loc]
        assert not big.exists()
        # walk (indeterminate), then 0..8 of 8: 5 files + .hidden + sub + big
        assert calls[0] == (0, 0)
        assert calls[1:] == [(i, 8) for i in range(9)]

    def test_delete_cancel_stops_between_entries(self, ftp_server):
        _host, port, root = ftp_server
        big = root / "big"
        big.mkdir()
        for i in range(5):
            (big / f"f{i}.txt").write_text("x")
        p = FtpProvider()
        _open(p, port)
        loc = VfsPath(scheme="ftp", root=f"bob@127.0.0.1:{port}", parts=("big",))
        ev = threading.Event()

        def _progress(i, n):
            if i == 2:
                ev.set()

        res = p.delete([loc], on_progress=_progress, cancel_event=ev)
        assert res.cancelled
        assert len(list(big.iterdir())) == 3

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


class _FailingStorConn:
    """Fake control connection: STOR opens, the server then replies 451."""

    def __init__(self, fail_on: str) -> None:
        self.fail_on = fail_on

    def voidcmd(self, cmd):
        return "200 ok"

    def transfercmd(self, cmd):
        if self.fail_on == "start":
            raise ftplib.error_temp("451 Failure writing to local file.")
        a, b = socket.socketpair()
        self._peer = b
        return a

    def voidresp(self):
        raise ftplib.error_temp("451 Failure writing to local file.")


@pytest.mark.parametrize("fail_on", ["start", "close"])
def test_upload_server_451_is_oserror_not_a_crash(fail_on):
    """A server-side write failure (disk full on the FTP host) used to escape
    as ftplib.error_temp, which the engine does not catch — the app crashed.
    It must surface as OSError, and the connection lock must be released."""
    from dunders.fm.providers.ftp_provider import _FtpWriter

    lock = threading.Lock()
    w = _FtpWriter(_FailingStorConn(fail_on), lock, "/x.bin")
    with pytest.raises(OSError, match="451"):
        w.write(b"data")
        w.close()
    assert not lock.locked()
