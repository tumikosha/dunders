"""netrc_store — cross-platform credential persistence via ~/.netrc."""

import os
import stat

import pytest

from dunders.fm import netrc_store


@pytest.fixture
def netrc_file(tmp_path, monkeypatch):
    p = tmp_path / ".netrc"
    monkeypatch.setattr(netrc_store, "netrc_path", lambda: p)
    return p


def test_lookup_missing_file_is_none(netrc_file):
    assert netrc_store.lookup("host") is None


def test_save_then_lookup_roundtrip(netrc_file):
    netrc_store.save("host", "bob", "s3cret")
    assert netrc_store.lookup("host") == ("bob", "s3cret")


def test_lookup_absent_host_is_none(netrc_file):
    netrc_store.save("host", "bob", "pw")
    assert netrc_store.lookup("other") is None


def test_save_updates_existing_entry_no_duplicate(netrc_file):
    netrc_store.save("host", "bob", "old")
    netrc_store.save("host", "bob", "new")
    assert netrc_store.lookup("host") == ("bob", "new")
    # only one machine line for that host
    lines = [ln for ln in netrc_file.read_text().splitlines()
             if ln.split()[:2] == ["machine", "host"]]
    assert len(lines) == 1


def test_save_preserves_other_hosts(netrc_file):
    netrc_store.save("h1", "a", "p1")
    netrc_store.save("h2", "b", "p2")
    assert netrc_store.lookup("h1") == ("a", "p1")
    assert netrc_store.lookup("h2") == ("b", "p2")


def test_saved_file_is_chmod_600(netrc_file):
    netrc_store.save("host", "bob", "pw")
    mode = stat.S_IMODE(os.stat(netrc_file).st_mode)
    assert mode == 0o600


def test_lookup_tolerates_malformed_file(netrc_file):
    netrc_file.write_text("this is not valid netrc !!!\n")
    assert netrc_store.lookup("host") is None


class TestProvidersConsultNetrc:
    def test_ftp_skips_prompt_when_netrc_has_host(self, monkeypatch):
        from dunders.fm.providers.ftp_provider import FtpProvider
        monkeypatch.setattr(netrc_store, "lookup", lambda h: ("bob", "pw"))
        assert FtpProvider().needs_password("bob@host/") is False

    def test_ftp_prompts_when_netrc_empty(self, monkeypatch):
        from dunders.fm.providers.ftp_provider import FtpProvider
        monkeypatch.setattr(netrc_store, "lookup", lambda h: None)
        assert FtpProvider().needs_password("bob@host/") is True

    def test_sftp_skips_prompt_when_netrc_has_host(self, monkeypatch):
        paramiko = pytest.importorskip("paramiko")  # noqa: F841
        from dunders.fm.providers.sftp_provider import SftpProvider
        monkeypatch.setattr(
            "dunders.fm.providers.sftp_provider._have_local_keys", lambda: False
        )
        monkeypatch.setattr(netrc_store, "lookup", lambda h: ("bob", "pw"))
        assert SftpProvider().needs_password("bob@host/") is False
