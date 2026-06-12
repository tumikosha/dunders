"""Tests for the shared clipboard helper (system buffer + OSC 52 fallback)."""

from dunders.windowing.core import clipboard


class _FakeApp:
    def __init__(self, clip=""):
        self.clipboard = clip
        self.osc52 = []

    def copy_to_clipboard(self, text):
        self.osc52.append(text)


def test_copy_writes_system_and_osc52(monkeypatch):
    sent = []
    monkeypatch.setattr(clipboard, "system_copy", lambda t: sent.append(t) or True)
    app = _FakeApp()
    clipboard.copy("hello", app=app)
    assert sent == ["hello"]
    assert app.osc52 == ["hello"]


def test_copy_without_app_only_system(monkeypatch):
    sent = []
    monkeypatch.setattr(clipboard, "system_copy", lambda t: sent.append(t) or True)
    clipboard.copy("hello")
    assert sent == ["hello"]


def test_paste_prefers_system(monkeypatch):
    monkeypatch.setattr(clipboard, "system_paste", lambda: "from-system")
    app = _FakeApp(clip="from-app")
    assert clipboard.paste(app=app) == "from-system"


def test_paste_falls_back_to_app_clipboard(monkeypatch):
    monkeypatch.setattr(clipboard, "system_paste", lambda: "")
    app = _FakeApp(clip="from-app")
    assert clipboard.paste(app=app) == "from-app"


def test_paste_empty_when_nothing_available(monkeypatch):
    monkeypatch.setattr(clipboard, "system_paste", lambda: "")
    assert clipboard.paste() == ""
