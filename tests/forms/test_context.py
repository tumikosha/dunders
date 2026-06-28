import subprocess
from types import SimpleNamespace

from dunders.forms import context


def test_reads_first_available_tool(monkeypatch):
    monkeypatch.setattr(context.sys, "platform", "linux")
    monkeypatch.setattr(context.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout="hello world\n")

    monkeypatch.setattr(context.subprocess, "run", fake_run)
    assert context.read_clipboard() == "hello world"


def test_missing_tool_returns_empty(monkeypatch):
    monkeypatch.setattr(context.sys, "platform", "linux")
    monkeypatch.setattr(context.shutil, "which", lambda name: None)
    assert context.read_clipboard() == ""


def test_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr(context.sys, "platform", "darwin")
    monkeypatch.setattr(context.shutil, "which", lambda name: "/usr/bin/pbpaste")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1.0)

    monkeypatch.setattr(context.subprocess, "run", boom)
    assert context.read_clipboard() == ""
