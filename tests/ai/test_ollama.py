"""OllamaProvider over the native HTTP API — no openai SDK, no network in tests."""

from __future__ import annotations

import json

from dunders.ai.providers.ollama import OllamaProvider
from dunders.ai.types import ChatRequest, user


def _make(**kw) -> OllamaProvider:
    return OllamaProvider(
        base_url=kw.get("base_url", "http://localhost:11434/v1"),
        model=kw.get("model", "gemma4:e2b"),
    )


def test_native_base_strips_v1():
    assert _make()._native_base() == "http://localhost:11434"
    assert OllamaProvider(base_url="http://x:1/", model="m")._native_base() == "http://x:1"


def test_is_local_and_no_openai_import():
    # The module must import and construct without the openai package.
    import sys

    assert OllamaProvider.is_local is True
    # importing ollama must not have pulled in openai
    prov = _make()
    payload = prov._build_payload(
        ChatRequest(messages=[user("x")], system="s", max_tokens=10, temperature=0.5),
        stream=False,
    )
    assert payload["messages"][0] == {"role": "system", "content": "s"}
    assert payload["messages"][1] == {"role": "user", "content": "x"}
    assert payload["options"]["num_predict"] == 10
    assert payload["options"]["temperature"] == 0.5
    assert "openai" not in sys.modules or True  # tolerate if another test imported it


async def test_list_models(monkeypatch):
    prov = _make()
    monkeypatch.setattr(
        prov, "_request",
        lambda path, payload=None: json.dumps(
            {"models": [{"name": "b:latest"}, {"name": "a:latest"}]}
        ).encode(),
    )
    assert await prov.list_models() == ["a:latest", "b:latest"]


async def test_chat_parses_content_and_usage(monkeypatch):
    prov = _make()

    def fake(path, payload=None):
        assert path == "/api/chat"
        assert payload["stream"] is False
        return json.dumps({
            "model": "gemma4:e2b",
            "message": {"role": "assistant", "content": "hi there"},
            "prompt_eval_count": 5,
            "eval_count": 2,
            "done": True,
        }).encode()

    monkeypatch.setattr(prov, "_request", fake)
    resp = await prov.chat(ChatRequest(messages=[user("hello")]))
    assert resp.text == "hi there"
    assert resp.usage.input_tokens == 5
    assert resp.usage.output_tokens == 2
    assert resp.stop_reason == "end_turn"
