# tests/core/vfs/test_provider_preview.py
from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import PreviewResult, ProviderPreview


def test_preview_result_fields():
    r = PreviewResult(text="hi", kind="log", title="logs: web")
    assert (r.text, r.kind, r.title) == ("hi", "log", "logs: web")


def test_provider_preview_is_structural():
    class P:
        scheme = "x"
        def preview(self, loc, entry):
            return None
    assert isinstance(P(), ProviderPreview)

    class Q:
        scheme = "y"
    assert not isinstance(Q(), ProviderPreview)
