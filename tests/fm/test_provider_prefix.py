"""Provider-declared prefixes: '<scheme>:<spec>' F5 destinations resolve via
the matching provider's resolve_target, generically (not zip-hardcoded)."""

import zipfile

import pytest

from dunders.core.vfs import VfsPath
from dunders.core.vfs.provider import TargetResolver
from dunders.fm.dialogs import CopyMoveDialog
from dunders.fm.providers.zip_provider import ZipProvider
from dunders.windowing import Desktop, Window


class TestZipResolveTarget:
    def test_conforms_to_target_resolver(self):
        assert isinstance(ZipProvider(), TargetResolver)

    def test_relative_name_under_base(self, tmp_path):
        base = VfsPath.local(tmp_path)
        loc = ZipProvider().resolve_target("backup.zip", base=base)
        assert loc == VfsPath(scheme="zip", root=str(tmp_path / "backup.zip"), parts=())

    def test_appends_zip_suffix(self, tmp_path):
        loc = ZipProvider().resolve_target("backup", base=VfsPath.local(tmp_path))
        assert loc.root.endswith("backup.zip")

    def test_empty_spec_defaults(self, tmp_path):
        loc = ZipProvider().resolve_target("", base=VfsPath.local(tmp_path))
        assert loc.root.endswith("archive.zip")

    def test_non_file_base_rejected(self):
        base = VfsPath(scheme="zip", root="/a.zip", parts=())
        assert ZipProvider().resolve_target("x.zip", base=base) is None


class TestGenericDispatch:
    """The app resolves any registered prefix, not just zip."""

    def test_resolver_called_for_custom_scheme(self, tmp_path):
        from dunders.app import DundersApp

        sentinel = VfsPath(scheme="demo", root="/made/up", parts=())

        class _DemoProvider:
            scheme = "demo"
            capabilities = frozenset({"read"})
            def resolve_target(self, spec, *, base):
                assert spec == "thing"
                return sentinel

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        app._vfs_registry.register(_DemoProvider())
        got = app._resolve_prefixed_target("demo:thing", base=VfsPath.local(tmp_path))
        assert got is sentinel

    def test_plain_path_is_not_a_prefix(self, tmp_path):
        from dunders.app import DundersApp

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        # No "<scheme>:" → ordinary path, resolver returns None.
        assert app._resolve_prefixed_target("/some/dir", base=VfsPath.local(tmp_path)) is None

    def test_provider_without_resolver_returns_none(self, tmp_path):
        from dunders.app import DundersApp

        app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
        # "file:" is registered but LocalProvider has no resolve_target.
        assert app._resolve_prefixed_target("file:/x", base=VfsPath.local(tmp_path)) is None


def _panels(app):
    desktop = app.query_one(Desktop)
    left = desktop.query_one("#panel-left", Window).content
    right = desktop.query_one("#panel-right", Window).content
    return left, right


@pytest.mark.asyncio
async def test_f5_prefix_creates_archive_and_opens_it_in_panel(tmp_path):
    from dunders.app import DundersApp

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hi")
    (src / "dir").mkdir()
    (src / "dir" / "inner.txt").write_text("in")
    dst = tmp_path / "dst"
    dst.mkdir()

    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        left.cursor = next(i for i, e in enumerate(left.entries) if e.name == "dir")
        await pilot.press("f5")
        await pilot.pause()
        app.query_one(CopyMoveDialog)._input.value = "zip:bundle.zip"
        app.query_one(CopyMoveDialog).action_submit()
        for _ in range(20):
            await pilot.pause()
        out = dst / "bundle.zip"
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            assert "dir/inner.txt" in zf.namelist()
        # The destination panel is now browsing INSIDE the new archive.
        assert right.cwd_loc == VfsPath(scheme="zip", root=str(out), parts=())


class TestLocalResolveTarget:
    """LocalProvider is an openable dunder ("Local files" in the "_" menu) — a
    one-keystroke way back to the local filesystem; empty spec = home."""

    def test_conforms_to_target_resolver(self):
        from dunders.fm.vfs_local import LocalProvider
        assert isinstance(LocalProvider(), TargetResolver)

    def test_openable_metadata(self):
        from dunders.fm.vfs_local import LocalProvider
        p = LocalProvider()
        assert p.display_name == "Local files"
        assert p.accepts_empty_open is True

    def test_empty_spec_is_home(self):
        from pathlib import Path
        from dunders.fm.vfs_local import LocalProvider
        loc = LocalProvider().resolve_target("", base=VfsPath.local("/"))
        assert loc == VfsPath.local(Path.home())

    def test_absolute_path(self, tmp_path):
        from dunders.fm.vfs_local import LocalProvider
        loc = LocalProvider().resolve_target(str(tmp_path), base=VfsPath.local("/"))
        assert loc == VfsPath.local(tmp_path.resolve())

    def test_relative_resolves_under_base(self, tmp_path):
        from dunders.fm.vfs_local import LocalProvider
        (tmp_path / "sub").mkdir()
        loc = LocalProvider().resolve_target("sub", base=VfsPath.local(tmp_path))
        assert loc == VfsPath.local((tmp_path / "sub").resolve())

    def test_non_directory_raises(self, tmp_path):
        from dunders.fm.vfs_local import LocalProvider
        f = tmp_path / "f.txt"
        f.write_text("x")
        with pytest.raises(OSError):
            LocalProvider().resolve_target(str(f), base=VfsPath.local("/"))

    def test_appears_in_registry_as_openable(self):
        from dunders.fm.vfs_local import default_registry
        reg = default_registry()
        prov = reg.for_scheme("file")
        assert getattr(prov, "display_name", None) == "Local files"
        assert hasattr(prov, "resolve_target")
