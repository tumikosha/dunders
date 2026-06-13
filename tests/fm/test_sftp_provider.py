"""SftpProvider — spec parsing / error classifier / needs_password (no server)
plus integration against an in-process paramiko SFTP server backed by a temp dir.
"""

import os
import socket
import threading

import pytest

from dunders.core.vfs import VfsPath

try:
    import paramiko
    from dunders.fm.providers.sftp_provider import (
        SftpProvider,
        _canonical_root,
        _connect_error,
        _parse_spec,
    )
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False

_needs_paramiko = pytest.mark.skipif(not _HAS_PARAMIKO, reason="paramiko not installed")


# ---- pure logic (no server) ----------------------------------------------

@_needs_paramiko
class TestParseAndClassify:
    def test_parse_user_pass_host_port_path(self):
        assert _parse_spec("bob:pw@h:2222/srv/x") == ("h", 2222, "bob", "pw", "srv/x")

    def test_default_port_22(self):
        assert _parse_spec("host")[1] == 22

    def test_canonical_root(self):
        assert _canonical_root("h", 22, "bob") == "bob@h:22"

    def test_connect_error_auth(self):
        msg = _connect_error(paramiko.AuthenticationException(), "h", 22, "bob")
        assert "auth failed" in msg.lower() and "bob" in msg

    def test_connect_error_unknown_host(self):
        assert "Unknown host" in _connect_error(socket.gaierror(), "nope", 22, "u")

    def test_invalid_port_raises(self):
        with pytest.raises(OSError) as ei:
            SftpProvider().resolve_target("u@h:99999/", base=VfsPath.local("/"))
        assert "99999" in str(ei.value)


@_needs_paramiko
class TestNeedsPassword:
    def test_prompts_when_no_keys(self, monkeypatch):
        monkeypatch.setattr(
            "dunders.fm.providers.sftp_provider._have_local_keys", lambda: False
        )
        assert SftpProvider().needs_password("bob@host/") is True

    def test_no_prompt_when_keys_available(self, monkeypatch):
        monkeypatch.setattr(
            "dunders.fm.providers.sftp_provider._have_local_keys", lambda: True
        )
        assert SftpProvider().needs_password("bob@host/") is False

    def test_inline_password_no_prompt(self):
        assert SftpProvider().needs_password("bob:pw@host/") is False


# ---- in-process SFTP server (paramiko stub backed by a temp dir) ----------

_HOST_KEY = None


def _host_key():
    global _HOST_KEY
    if _HOST_KEY is None and _HAS_PARAMIKO:
        _HOST_KEY = paramiko.RSAKey.generate(2048)
    return _HOST_KEY


if _HAS_PARAMIKO:
    class _StubServer(paramiko.ServerInterface):
        def __init__(self, password):
            self._password = password

        def check_auth_password(self, username, password):
            return (paramiko.AUTH_SUCCESSFUL if password == self._password
                    else paramiko.AUTH_FAILED)

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED

    class _StubHandle(paramiko.SFTPHandle):
        def stat(self):
            try:
                return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)

    def _iface_for(root: str):
        class _Iface(paramiko.SFTPServerInterface):
            ROOT = root

            def _real(self, path):
                return os.path.join(self.ROOT, self.canonicalize(path).lstrip("/"))

            def list_folder(self, path):
                p = self._real(path)
                try:
                    out = []
                    for fn in os.listdir(p):
                        a = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(p, fn)))
                        a.filename = fn
                        out.append(a)
                    return out
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def stat(self, path):
                try:
                    return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            lstat = stat

            def open(self, path, flags, attr):
                p = self._real(path)
                try:
                    fd = os.open(p, flags, 0o666)
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)
                if flags & os.O_WRONLY:
                    mode = "ab" if flags & os.O_APPEND else "wb"
                elif flags & os.O_RDWR:
                    mode = "a+b" if flags & os.O_APPEND else "r+b"
                else:
                    mode = "rb"
                f = os.fdopen(fd, mode)
                h = _StubHandle(flags)
                h.filename = p
                h.readfile = h.writefile = f
                return h

            def remove(self, path):
                try:
                    os.remove(self._real(path))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def mkdir(self, path, attr):
                try:
                    os.mkdir(self._real(path))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

            def rmdir(self, path):
                try:
                    os.rmdir(self._real(path))
                    return paramiko.SFTP_OK
                except OSError as e:
                    return paramiko.SFTPServer.convert_errno(e.errno)

        return _Iface


@pytest.fixture
def sftp_server(tmp_path):
    root = tmp_path / "sftproot"
    root.mkdir()
    (root / "hello.txt").write_text("hi there")
    (root / "dir").mkdir()
    (root / "dir" / "inner.txt").write_text("inner")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    stop = threading.Event()
    transports = []

    def accept_loop():
        sock.settimeout(0.3)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break
            t = paramiko.Transport(conn)
            t.add_server_key(_host_key())
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, _iface_for(str(root)))
            t.start_server(server=_StubServer("secret"))
            transports.append(t)

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", port, root
    finally:
        stop.set()
        for t in transports:
            try:
                t.close()
            except Exception:
                pass
        sock.close()
        thread.join(timeout=3)


def _open(provider, port):
    return provider.resolve_target(
        f"bob:secret@127.0.0.1:{port}/", base=VfsPath.local("/")
    )


def _root_loc(port):
    return VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=())


@_needs_paramiko
class TestIntegration:
    def test_resolve_and_scan(self, sftp_server):
        _h, port, _root = sftp_server
        p = SftpProvider()
        loc = _open(p, port)
        assert loc == _root_loc(port)
        by = {e.name: e for e in p.scan(loc, include_parent=False)}
        assert set(by) == {"hello.txt", "dir"}
        assert by["dir"].is_dir and not by["hello.txt"].is_dir
        assert by["hello.txt"].size == len("hi there")

    def test_descend_and_read(self, sftp_server):
        _h, port, _root = sftp_server
        p = SftpProvider()
        _open(p, port)
        sub = VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=("dir",))
        assert {e.name for e in p.scan(sub, include_parent=False)} == {"inner.txt"}
        f = VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        with p.open_read(f) as fh:
            assert fh.read() == b"hi there"

    def test_upload_and_mkdir(self, sftp_server):
        _h, port, root = sftp_server
        p = SftpProvider()
        _open(p, port)
        loc = VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=("up.txt",))
        with p.open_write(loc) as w:
            w.write(b"uploaded")
        assert (root / "up.txt").read_bytes() == b"uploaded"
        p.mkdir(_root_loc(port), "newdir")
        assert (root / "newdir").is_dir()

    def test_no_clobber(self, sftp_server):
        _h, port, _root = sftp_server
        p = SftpProvider()
        _open(p, port)
        loc = VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        with pytest.raises(OSError):
            p.open_write(loc)

    def test_delete_file_and_dir(self, sftp_server):
        _h, port, root = sftp_server
        p = SftpProvider()
        _open(p, port)
        f = VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=("hello.txt",))
        assert not p.delete([f]).errors
        assert not (root / "hello.txt").exists()
        d = VfsPath(scheme="sftp", root=f"bob@127.0.0.1:{port}", parts=("dir",))
        assert not p.delete([d]).errors
        assert not (root / "dir").exists()

    def test_wrong_password_raises(self, sftp_server):
        _h, port, _root = sftp_server
        with pytest.raises(OSError) as ei:
            SftpProvider().resolve_target(
                f"bob:wrong@127.0.0.1:{port}/", base=VfsPath.local("/")
            )
        assert "auth failed" in str(ei.value).lower()


@_needs_paramiko
def test_registered_in_default_registry():
    from dunders.fm.vfs_local import default_registry
    assert "sftp" in default_registry().schemes()
