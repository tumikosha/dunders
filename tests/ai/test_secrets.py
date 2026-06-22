"""SecretResolver: file storage, 0600 perms, env override."""

from __future__ import annotations

import os
import stat

from dunders.ai.secrets import SecretResolver


def test_set_and_resolve(tmp_path):
    res = SecretResolver(tmp_path / "secrets.json")
    assert res.resolve("FOO_KEY") is None
    assert res.set("FOO_KEY", "s3cret") is True
    assert res.resolve("FOO_KEY") == "s3cret"


def test_file_is_0600(tmp_path):
    path = tmp_path / "secrets.json"
    res = SecretResolver(path)
    res.set("K", "v")
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_env_overrides_file(tmp_path, monkeypatch):
    res = SecretResolver(tmp_path / "secrets.json")
    res.set("OPENAI_API_KEY", "from-file")
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert res.resolve("OPENAI_API_KEY") == "from-env"


def test_delete(tmp_path):
    res = SecretResolver(tmp_path / "secrets.json")
    res.set("K", "v")
    assert res.delete("K") is True
    assert res.resolve("K") is None
