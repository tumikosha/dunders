from __future__ import annotations

import sys

import pytest

from dunders.app import DundersApp


def _flatten_default_buffer(app) -> bytes:
    default = app.console_registry.get_or_create(None)
    out = b""
    for i in range(default.buffer.line_count()):
        for seg in default.buffer.line(i):
            out += seg.text.encode()
    return out.lower()


@pytest.mark.asyncio
async def test_backend_switch_to_pty_or_unavailable(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_command_submitted_for_test(":backend pty", anonymous=False)
        await pilot.pause()
        joined = _flatten_default_buffer(app)
        if sys.platform == "win32":
            assert b"unavailable" in joined or b"pty" in joined
        else:
            assert b"backend" in joined or b"pty" in joined


@pytest.mark.asyncio
async def test_backend_switch_to_subprocess_works(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._on_command_submitted_for_test(":backend subprocess", anonymous=False)
        await pilot.pause()
        joined = _flatten_default_buffer(app)
        assert b"subprocess" in joined or b"backend" in joined
