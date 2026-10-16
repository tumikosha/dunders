"""Password-protected zip archives.

Provider: detects encryption, validates/remembers a password (memory only),
decrypts members, and keeps every write encrypted. Dialogs: an optional
password field where blank/whitespace means "no password". App: entering an
encrypted archive asks for its password in a modal first.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pyzipper
import pytest

from dunders.core.vfs import VfsPath
from dunders.fm.actions import pack_paths
from dunders.fm.dialogs import CopyMoveDialog, InputDialog, NewFileDialog, PackDialog
from dunders.fm.file_panel import FilePanel
from dunders.fm.providers.zip_provider import (
    ZipPasswordRequired,
    ZipProvider,
    normalize_password,
)

PW = "s3cret"


def _make_encrypted(path: Path, password: str = PW) -> Path:
    with pyzipper.AESZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode())
        zf.writestr("top.txt", b"hello")
        zf.writestr("dir/inner.txt", b"world")
    return path


def _make_plain(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("top.txt", b"hello")
    return path


def _read_all(path: Path, password: str | None) -> dict[str, bytes]:
    with pyzipper.AESZipFile(path) as zf:
        if password is not None:
            zf.setpassword(password.encode())
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


def _all_encrypted(path: Path) -> bool:
    with zipfile.ZipFile(path) as zf:
        return all(i.flag_bits & 0x1 for i in zf.infolist())


def _loc(path: Path, *parts: str) -> VfsPath:
    return VfsPath(scheme="zip", root=str(path), parts=parts)


class TestNormalizePassword:
    @pytest.mark.parametrize("value", [None, "", "   ", "\t \n"])
    def test_blank_means_none(self, value):
        assert normalize_password(value) is None

    def test_value_kept_verbatim(self):
        assert normalize_password(" a b ") == " a b "


class TestProvider:
    def test_detects_encryption(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        plain = _make_plain(tmp_path / "p.zip")
        assert prov.is_encrypted(str(enc))
        assert not prov.is_encrypted(str(plain))
        assert prov.needs_unlock(_loc(enc))
        assert not prov.needs_unlock(_loc(plain))

    def test_listing_works_without_password(self, tmp_path):
        enc = _make_encrypted(tmp_path / "e.zip")
        names = {e.name for e in ZipProvider().scan(_loc(enc), include_parent=False)}
        assert names == {"top.txt", "dir"}

    def test_check_password(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        assert prov.check_password(str(enc), PW)
        assert not prov.check_password(str(enc), "wrong")

    def test_read_requires_password(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        with pytest.raises(ZipPasswordRequired):
            prov.open_read(_loc(enc, "top.txt"))
        prov.set_password(str(enc), PW)
        assert not prov.needs_unlock(_loc(enc))
        assert prov.open_read(_loc(enc, "dir", "inner.txt")).read() == b"world"

    def test_append_stays_encrypted(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        prov.set_password(str(enc), PW)
        with prov.open_write(_loc(enc, "new.txt")) as w:
            w.write(b"added")
        assert _all_encrypted(enc)
        assert _read_all(enc, PW)["new.txt"] == b"added"

    def test_write_without_password_refused(self, tmp_path):
        enc = _make_encrypted(tmp_path / "e.zip")
        with pytest.raises(ZipPasswordRequired):
            ZipProvider().open_write(_loc(enc, "new.txt"))

    def test_overwrite_stays_encrypted(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        prov.set_password(str(enc), PW)
        with prov.open_write(_loc(enc, "top.txt"), overwrite=True) as w:
            w.write(b"edited")
        assert _all_encrypted(enc)
        data = _read_all(enc, PW)
        assert data["top.txt"] == b"edited"
        assert data["dir/inner.txt"] == b"world"

    def test_delete_stays_encrypted(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        prov.set_password(str(enc), PW)
        result = prov.delete([_loc(enc, "top.txt")])
        assert not result.errors
        assert _all_encrypted(enc)
        assert _read_all(enc, PW) == {"dir/inner.txt": b"world"}

    def test_delete_without_password_reports_error(self, tmp_path):
        enc = _make_encrypted(tmp_path / "e.zip")
        result = ZipProvider().delete([_loc(enc, "top.txt")])
        assert result.errors
        assert _read_all(enc, PW)["top.txt"] == b"hello"  # untouched

    def test_mkdir_stays_encrypted(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        prov.set_password(str(enc), PW)
        prov.mkdir(_loc(enc), "sub")
        assert _all_encrypted(enc)
        assert "sub" in {e.name for e in prov.scan(_loc(enc), include_parent=False)}

    @pytest.mark.skipif(shutil.which("zip") is None, reason="no zip CLI")
    def test_reads_legacy_zipcrypto(self, tmp_path):
        (tmp_path / "f.txt").write_text("legacy")
        subprocess.run(
            ["zip", "-q", "-P", PW, "old.zip", "f.txt"], cwd=tmp_path, check=True
        )
        prov = ZipProvider()
        archive = str(tmp_path / "old.zip")
        assert prov.is_encrypted(archive)
        assert not prov.check_password(archive, "wrong")
        assert prov.check_password(archive, PW)
        prov.set_password(archive, PW)
        loc = VfsPath(scheme="zip", root=archive, parts=("f.txt",))
        assert prov.open_read(loc).read() == b"legacy"


class TestResolveTarget:
    def test_new_archive_with_password_is_encrypted(self, tmp_path):
        prov = ZipProvider()
        loc = prov.resolve_target("new.zip", base=VfsPath.local(tmp_path), password=PW)
        with prov.open_write(loc.child("a.txt")) as w:
            w.write(b"data")
        out = tmp_path / "new.zip"
        assert _all_encrypted(out)
        assert _read_all(out, PW) == {"a.txt": b"data"}

    def test_whitespace_password_means_plain(self, tmp_path):
        prov = ZipProvider()
        loc = prov.resolve_target("new.zip", base=VfsPath.local(tmp_path), password="  ")
        with prov.open_write(loc.child("a.txt")) as w:
            w.write(b"data")
        out = tmp_path / "new.zip"
        assert not prov.is_encrypted(str(out))
        assert _read_all(out, None) == {"a.txt": b"data"}

    def test_existing_encrypted_wrong_password(self, tmp_path):
        _make_encrypted(tmp_path / "e.zip")
        with pytest.raises(PermissionError):
            ZipProvider().resolve_target(
                "e.zip", base=VfsPath.local(tmp_path), password="nope"
            )

    def test_existing_encrypted_right_password_unlocks(self, tmp_path):
        enc = _make_encrypted(tmp_path / "e.zip")
        prov = ZipProvider()
        loc = prov.resolve_target("e.zip", base=VfsPath.local(tmp_path), password=PW)
        assert not prov.needs_unlock(loc)
        assert prov.open_read(_loc(enc, "top.txt")).read() == b"hello"


class TestPack:
    def _src(self, tmp_path: Path) -> Path:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("alpha")
        return src

    def test_pack_with_password(self, tmp_path):
        src = self._src(tmp_path)
        out = tmp_path / "out.zip"
        result = pack_paths([src / "a.txt"], out, base=src, password=PW)
        assert not result.errors
        assert _all_encrypted(out)
        assert _read_all(out, PW) == {"a.txt": b"alpha"}

    def test_pack_whitespace_password_is_plain(self, tmp_path):
        src = self._src(tmp_path)
        out = tmp_path / "out.zip"
        pack_paths([src / "a.txt"], out, base=src, password="   ")
        with zipfile.ZipFile(out) as zf:
            assert zf.read("a.txt") == b"alpha"  # stdlib, no password


class TestPanelGuard:
    def test_bare_panel_does_not_enter_encrypted_archive(self, tmp_path):
        _make_encrypted(tmp_path / "e.zip")
        p = FilePanel(cwd=tmp_path)
        p.refresh_listing()
        p.cursor = next(i for i, e in enumerate(p.entries) if e.name == "e.zip")
        p.activate()
        assert p.cwd_loc == VfsPath.local(tmp_path)  # held back

    def test_plain_archive_enters(self, tmp_path):
        _make_plain(tmp_path / "p.zip")
        p = FilePanel(cwd=tmp_path)
        p.refresh_listing()
        p.cursor = next(i for i, e in enumerate(p.entries) if e.name == "p.zip")
        p.activate()
        assert p.cwd_loc.scheme == "zip"


# --------------------------------------------------------------------------
# App-level flows
# --------------------------------------------------------------------------


def _panels(app):
    from dunders.windowing import Desktop, Window

    desktop = app.query_one(Desktop)
    left = desktop.query_one("#panel-left", Window).content
    right = desktop.query_one("#panel-right", Window).content
    return left, right


async def _settle(pilot, n: int = 3) -> None:
    for _ in range(n):
        await pilot.pause()


async def _enter(app, pilot, name: str):
    panel = app._active_panel()
    panel.refresh_listing()
    panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == name)
    panel.activate()
    await _settle(pilot)
    return panel


@pytest.mark.asyncio
async def test_enter_encrypted_prompts_and_unlocks(tmp_path):
    from dunders.app import DundersApp

    enc = _make_encrypted(tmp_path / "e.zip")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _settle(pilot)
        panel = await _enter(app, pilot, "e.zip")
        dialog = app.query_one(InputDialog)
        assert panel.cwd_loc.scheme == "file"  # not entered yet

        # Wrong password: stays outside, asks again.
        dialog.set_value("wrong")
        dialog.action_submit()
        await _settle(pilot)
        assert panel.cwd_loc.scheme == "file"
        dialog = app.query_one(InputDialog)

        dialog.set_value(PW)
        dialog.action_submit()
        await _settle(pilot)
        assert panel.cwd_loc == _loc(enc)
        assert not app.query(InputDialog)
        prov = app._vfs_registry.for_scheme("zip")
        assert prov.open_read(_loc(enc, "top.txt")).read() == b"hello"


@pytest.mark.asyncio
async def test_enter_encrypted_cancel_stays(tmp_path):
    from dunders.app import DundersApp

    _make_encrypted(tmp_path / "e.zip")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _settle(pilot)
        panel = await _enter(app, pilot, "e.zip")
        app.query_one(InputDialog).action_cancel()
        await _settle(pilot)
        assert panel.cwd_loc == VfsPath.local(tmp_path)
        assert not app.query(InputDialog)


@pytest.mark.asyncio
async def test_enter_plain_archive_no_prompt(tmp_path):
    from dunders.app import DundersApp

    _make_plain(tmp_path / "p.zip")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _settle(pilot)
        panel = await _enter(app, pilot, "p.zip")
        assert panel.cwd_loc.scheme == "zip"
        assert not app.query(InputDialog)


@pytest.mark.asyncio
async def test_f5_zip_prefix_with_password(tmp_path):
    from dunders.app import DundersApp

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hi")
    dst = tmp_path / "dst"
    dst.mkdir()
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await _settle(pilot)
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        left.cursor = next(i for i, e in enumerate(left.entries) if e.name == "a.txt")
        await pilot.press("f5")
        await pilot.pause()
        dialog = app.query_one(CopyMoveDialog)
        row = dialog.query_one("#cm-password-row")
        assert row.has_class("-hidden")  # plain local destination
        dialog._input.value = "zip:bundle.zip"
        await pilot.pause()
        assert not row.has_class("-hidden")
        dialog._password_input.value = PW
        dialog.action_submit()
        await _settle(pilot, 20)
        out = dst / "bundle.zip"
        assert _all_encrypted(out)
        assert _read_all(out, PW) == {"a.txt": b"hi"}
        assert right.cwd_loc == _loc(out)  # entered without a second prompt


@pytest.mark.asyncio
async def test_pack_dialog_password(tmp_path):
    from dunders.app import DundersApp

    (tmp_path / "a.txt").write_text("alpha")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _settle(pilot)
        panel = app._active_panel()
        panel.cursor = next(i for i, e in enumerate(panel.entries) if e.name == "a.txt")
        app.action_pack()
        await pilot.pause()
        dialog = app.query_one(PackDialog)
        dialog._input.value = "packed.zip"
        dialog._password_input.value = PW
        dialog.action_submit()
        await _settle(pilot, 20)
        out = tmp_path / "packed.zip"
        assert _all_encrypted(out)
        assert _read_all(out, PW) == {"a.txt": b"alpha"}


@pytest.mark.asyncio
async def test_dunder_menu_zip_password(tmp_path):
    from dunders.app import DundersApp

    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _settle(pilot)
        panel = app._active_panel()
        app._open_dunder("zip")
        await pilot.pause()
        dialog = app.query_one(NewFileDialog)
        assert dialog._password_input is not None
        dialog._input.value = "fresh.zip"
        dialog._password_input.value = PW
        dialog.action_submit()
        await _settle(pilot)
        out = tmp_path / "fresh.zip"
        assert panel.cwd_loc == _loc(out)
        prov = app._vfs_registry.for_scheme("zip")
        with prov.open_write(_loc(out, "x.txt")) as w:
            w.write(b"x")
        assert _all_encrypted(out)


class TestReviewRegressions:
    def test_stale_password_is_forgotten_on_read_failure(self, tmp_path):
        prov = ZipProvider()
        enc = _make_encrypted(tmp_path / "e.zip")
        prov.set_password(str(enc), PW)
        enc.unlink()
        _make_encrypted(enc, password="other")  # archive replaced
        with pytest.raises(ZipPasswordRequired):
            prov.open_read(_loc(enc, "top.txt"))
        assert prov.needs_unlock(_loc(enc))  # next entry asks again

    def test_password_key_is_path_normalized(self, tmp_path):
        prov = ZipProvider()
        (tmp_path / "sub").mkdir()
        loc = prov.resolve_target(
            "../n.zip", base=VfsPath.local(tmp_path / "sub"), password=PW
        )
        assert loc is not None
        plain_path = _loc(tmp_path / "n.zip", "x.txt")  # same file, other spelling
        with prov.open_write(plain_path) as w:
            w.write(b"x")
        assert _all_encrypted(tmp_path / "n.zip")

    def test_check_password_skips_empty_members(self, tmp_path):
        path = tmp_path / "e.zip"
        with pyzipper.AESZipFile(
            path, "w", compression=zipfile.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(PW.encode())
            zf.writestr("empty.txt", b"")
            zf.writestr("data.txt", b"payload" * 100)
        prov = ZipProvider()
        assert prov.check_password(str(path), PW)
        assert not prov.check_password(str(path), "wrong")


@pytest.mark.asyncio
async def test_f5_into_existing_encrypted_without_password_refuses(tmp_path):
    from dunders.app import DundersApp

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hi")
    dst = tmp_path / "dst"
    dst.mkdir()
    enc = _make_encrypted(dst / "e.zip")
    app = DundersApp(launch_mode="fm", initial_path=str(src))
    async with app.run_test() as pilot:
        await _settle(pilot)
        left, right = _panels(app)
        right.cwd = dst
        right.refresh_listing()
        left.cursor = next(i for i, e in enumerate(left.entries) if e.name == "a.txt")
        await pilot.press("f5")
        await pilot.pause()
        dialog = app.query_one(CopyMoveDialog)
        dialog._input.value = "zip:e.zip"
        await pilot.pause()
        dialog.action_submit()
        await _settle(pilot, 10)
        assert set(_read_all(enc, PW)) == {"top.txt", "dir/inner.txt"}  # untouched
        assert right.cwd_loc == VfsPath.local(dst)


@pytest.mark.asyncio
async def test_repack_with_new_password_replaces_remembered_one(tmp_path):
    from dunders.app import DundersApp

    (tmp_path / "a.txt").write_text("alpha")
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test() as pilot:
        await _settle(pilot)
        panel = app._active_panel()
        prov = app._vfs_registry.for_scheme("zip")
        out = tmp_path / "packed.zip"
        for pw in ("first", "second"):
            out.unlink(missing_ok=True)
            panel.refresh_listing()
            panel.cursor = next(
                i for i, e in enumerate(panel.entries) if e.name == "a.txt"
            )
            app.action_pack()
            await pilot.pause()
            dialog = app.query_one(PackDialog)
            dialog._input.value = "packed.zip"
            dialog._password_input.value = pw
            dialog.action_submit()
            await _settle(pilot, 20)
        loc = _loc(out, "a.txt")
        assert not prov.needs_unlock(loc)
        assert prov.open_read(loc).read() == b"alpha"
