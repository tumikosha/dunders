"""LlmService: role resolution, FakeProvider chat/stream, guardrail wiring."""

from __future__ import annotations

import asyncio

import pytest

from dunders.ai.config import AiConfig
from dunders.ai.provider import NoAiZoneError, ProviderUnavailable
from dunders.ai.providers.fake import FakeProvider
from dunders.ai.guardrails import NOAI_MARKER
from dunders.ai.service import LlmService
from dunders.ai.types import MessageDone, TextDelta, user


class CloudFake(FakeProvider):
    """A FakeProvider that the service treats as a cloud provider."""

    name = "cloudfake"


def _service(roles: dict, **kw) -> LlmService:
    cfg = AiConfig.from_dict({"roles": roles, "guardrails": kw.get("guardrails", {})})
    svc = LlmService(config=cfg)
    return svc


async def test_chat_echo_via_fake():
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})
    resp = await svc.chat([user("hello")])
    assert resp.text == "echo: hello"
    assert resp.model == "fake-1"


async def test_unconfigured_role_raises():
    svc = _service({"default": {}})
    with pytest.raises(ProviderUnavailable):
        await svc.chat([user("x")])


async def test_stream_emits_deltas_then_done():
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})
    events = [ev async for ev in svc.stream([user("a b c")])]
    assert any(isinstance(e, TextDelta) for e in events)
    assert isinstance(events[-1], MessageDone)
    assert events[-1].response.text == "echo: a b c"


async def test_usage_metered():
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})
    await svc.chat([user("count me")])
    assert svc.usage_total().input_tokens > 0


async def test_cache_avoids_second_provider_call():
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})
    await svc.chat([user("same")])
    await svc.chat([user("same")])
    prov = svc.provider_for(role="default")
    assert len(prov.calls) == 1  # second was a cache hit


async def test_cache_disabled_per_call():
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})
    await svc.chat([user("same")], cache=False)
    await svc.chat([user("same")], cache=False)
    prov = svc.provider_for(role="default")
    assert len(prov.calls) == 2


async def test_cloud_provider_redacts_pii():
    svc = _service({"default": {"provider": "cloudfake", "model": "c"}})
    svc.register_provider(CloudFake)
    resp = await svc.chat([user("write to a@b.com")])
    # CloudFake echoes the (redacted) prompt
    assert "a@b.com" not in resp.text
    assert "[REDACTED_EMAIL]" in resp.text


async def test_no_ai_zone_blocks_cloud(tmp_path):
    (tmp_path / NOAI_MARKER).touch()
    svc = _service({"default": {"provider": "cloudfake", "model": "c"}})
    svc.register_provider(CloudFake)
    with pytest.raises(NoAiZoneError):
        await svc.chat([user("x")], path=tmp_path / "f.txt")


async def test_local_provider_ignores_no_ai_zone(tmp_path):
    (tmp_path / NOAI_MARKER).touch()
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})
    # fake is treated as local -> allowed even in a no-AI zone
    resp = await svc.chat([user("x")], path=tmp_path / "f.txt")
    assert resp.text == "echo: x"


async def test_override_provider_and_model():
    svc = _service({"default": {}})
    svc.register_provider(CloudFake)
    resp = await svc.chat([user("hi")], provider="cloudfake", model="c2")
    assert resp.model == "c2"


def test_run_sync_bridges_to_loop():
    svc = _service({"default": {"provider": "fake", "model": "fake-1"}})

    async def runner():
        svc.set_loop(asyncio.get_running_loop())
        # chat_sync blocks on the loop via run_coroutine_threadsafe, so it must
        # run off the loop thread — asyncio.to_thread keeps the loop free to
        # service the scheduled coroutine.
        return await asyncio.to_thread(svc.chat_sync, [user("bridged")])

    resp = asyncio.run(runner())
    assert resp.text == "echo: bridged"
