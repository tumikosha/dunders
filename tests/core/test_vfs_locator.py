"""Tests for VfsPath — the scheme-agnostic locator that replaces bare Path
in VFS contracts.

VfsPath must satisfy three jobs:
  1. Round-trip with pathlib for the ``file`` scheme (so the existing
     local-filesystem code can keep working through a bridge).
  2. Navigate (child / parent / name) uniformly for ANY scheme, so a panel
     walks a zip or an sftp tree the same way it walks local dirs.
  3. Serialise to / from a URI string for display and for the future
     ``api``/``zip`` providers.
"""

from pathlib import Path

from dunders.core.vfs import VfsPath


class TestLocalBridge:
    def test_local_round_trip_absolute(self):
        p = Path("/home/user/a.txt")
        loc = VfsPath.local(p)
        assert loc.scheme == "file"
        assert loc.to_local() == p

    def test_local_round_trip_root(self):
        loc = VfsPath.local(Path("/"))
        assert loc.to_local() == Path("/")

    def test_local_parts_exclude_anchor(self):
        loc = VfsPath.local(Path("/home/user"))
        assert loc.parts == ("home", "user")

    def test_to_local_rejects_non_file_scheme(self):
        loc = VfsPath(scheme="zip", root="/home/a.zip", parts=("inner",))
        try:
            loc.to_local()
        except ValueError:
            pass
        else:
            raise AssertionError("to_local() must raise for non-file schemes")


class TestNavigation:
    def test_name(self):
        loc = VfsPath(scheme="zip", root="/a.zip", parts=("dir", "file.txt"))
        assert loc.name == "file.txt"

    def test_child(self):
        loc = VfsPath(scheme="zip", root="/a.zip", parts=("dir",))
        child = loc.child("file.txt")
        assert child.parts == ("dir", "file.txt")
        assert child.scheme == "zip" and child.root == "/a.zip"

    def test_parent(self):
        loc = VfsPath(scheme="zip", root="/a.zip", parts=("dir", "file.txt"))
        parent = loc.parent
        assert parent is not None
        assert parent.parts == ("dir",)

    def test_parent_at_source_root_is_none(self):
        loc = VfsPath(scheme="zip", root="/a.zip", parts=())
        assert loc.parent is None

    def test_is_source_root(self):
        assert VfsPath(scheme="file", root="/", parts=()).is_source_root
        assert not VfsPath(scheme="file", root="/", parts=("home",)).is_source_root


class TestUri:
    def test_file_uri_round_trip(self):
        loc = VfsPath.local(Path("/home/user/a.txt"))
        uri = loc.as_uri()
        assert uri == "file:///home/user/a.txt"
        assert VfsPath.parse(uri) == loc

    def test_file_uri_with_space(self):
        loc = VfsPath.local(Path("/home/user/my file.txt"))
        assert VfsPath.parse(loc.as_uri()) == loc

    def test_other_scheme_uri_round_trip(self):
        loc = VfsPath(scheme="zip", root="/home/a.zip", parts=("inner", "f.txt"))
        uri = loc.as_uri()
        assert uri == "zip:///home/a.zip!/inner/f.txt"
        assert VfsPath.parse(uri) == loc

    def test_other_scheme_uri_no_parts(self):
        loc = VfsPath(scheme="docker", root="abc123", parts=())
        uri = loc.as_uri()
        assert uri == "docker://abc123"
        assert VfsPath.parse(uri) == loc


class TestValueSemantics:
    def test_frozen_hashable_in_set(self):
        a = VfsPath.local(Path("/x/y"))
        b = VfsPath.local(Path("/x/y"))
        assert a == b
        assert len({a, b}) == 1

    def test_str_is_uri(self):
        loc = VfsPath.local(Path("/home/user"))
        assert str(loc) == loc.as_uri()


class TestDisplay:
    """display() — human-facing location string for the header / copy button
    (clean URLs, no archive '!' separator; NOT round-trippable)."""

    def test_file_is_local_path(self, tmp_path):
        assert VfsPath.local(tmp_path).display() == str(tmp_path)

    def test_sftp_with_path(self):
        loc = VfsPath(scheme="sftp", root="bob@host:22", parts=("dir", "file"))
        assert loc.display() == "sftp://bob@host:22/dir/file"

    def test_sftp_root_only(self):
        loc = VfsPath(scheme="sftp", root="bob@host:22", parts=())
        assert loc.display() == "sftp://bob@host:22"

    def test_no_bang_separator(self):
        loc = VfsPath(scheme="sftp", root="bob@host:22", parts=("a", "b"))
        assert "!" not in loc.display()

    def test_docker_local_container(self):
        loc = VfsPath(scheme="docker", root="", parts=("web", "etc"))
        assert loc.display() == "docker:web/etc"

    def test_docker_local_index(self):
        assert VfsPath(scheme="docker", root="", parts=()).display() == "docker:"

    def test_docker_remote(self):
        loc = VfsPath(scheme="docker", root="ssh://u@h:22", parts=("web",))
        assert loc.display() == "docker://ssh://u@h:22/web"

    def test_zip_archive(self):
        loc = VfsPath(scheme="zip", root="/a/b.zip", parts=("inner",))
        assert loc.display() == "zip:///a/b.zip/inner"
