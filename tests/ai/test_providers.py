"""Provider message mapping (pure) + error normalization (SDK-gated)."""

from __future__ import annotations


from dunders.ai.providers.anthropic import _to_anthropic_messages
from dunders.ai.providers.fake import FakeProvider
from dunders.ai.providers.openai_compat import _to_openai_messages, _to_openai_tool
from dunders.ai.types import (
    ChatRequest,
    ImageBlock,
    Message,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    user,
)


# --- pure mapping ----------------------------------------------------------


def test_openai_string_message():
    out = _to_openai_messages([user("hi")], system="sys")
    assert out[0] == {"role": "system", "content": "sys"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_openai_tool_result_becomes_tool_role():
    msg = Message(role="user", content=[ToolResultBlock("call_1", "42")])
    out = _to_openai_messages([msg], system=None)
    assert out[0]["role"] == "tool"
    assert out[0]["tool_call_id"] == "call_1"
    assert out[0]["content"] == "42"


def test_openai_image_block():
    msg = Message(role="user", content=[ImageBlock(data="QQ==", media_type="image/png")])
    out = _to_openai_messages([msg], system=None)
    parts = out[0]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_tool_schema():
    spec = ToolSpec("get", "desc", {"type": "object"})
    t = _to_openai_tool(spec)
    assert t["type"] == "function"
    assert t["function"]["name"] == "get"
    assert t["function"]["parameters"] == {"type": "object"}


def test_anthropic_system_excluded_from_messages():
    from dunders.ai.types import system_msg

    out = _to_anthropic_messages([system_msg("sys"), user("hi")])
    assert all(m["role"] != "system" for m in out)
    assert out[0]["role"] == "user"


def test_anthropic_tool_use_and_result_blocks():
    msgs = [
        Message(role="assistant", content=[ToolUseBlock("id1", "f", {"a": 1})]),
        Message(role="user", content=[ToolResultBlock("id1", "ok")]),
    ]
    out = _to_anthropic_messages(msgs)
    assert out[0]["content"][0]["type"] == "tool_use"
    assert out[1]["content"][0]["type"] == "tool_result"


def test_provider_capabilities_are_frozensets():
    assert isinstance(FakeProvider.capabilities, frozenset)


async def test_fake_list_models():
    prov = FakeProvider(model="fake-1")
    models = await prov.list_models()
    assert "fake-1" in models
    assert len(models) >= 1


# --- HTTP status mapping (stdlib, no SDK) ----------------------------------


def test_http_status_mapping():
    from dunders.ai.providers import _http
    from dunders.ai.provider import (
        AiError,
        AuthError,
        ProviderUnavailable,
        RateLimitError,
    )

    assert isinstance(_http.map_status(401, "Unauthorized"), AuthError)
    assert isinstance(_http.map_status(403, "Forbidden"), AuthError)
    assert isinstance(_http.map_status(429, "Too Many"), RateLimitError)
    assert isinstance(_http.map_status(404, "Not Found"), ProviderUnavailable)
    assert isinstance(_http.map_status(500, "Server Error"), AiError)


def test_http_sets_user_agent():
    # The stdlib default UA ("Python-urllib") is Cloudflare-banned (groq 1010);
    # _http must send a real User-Agent, overridable by the caller.
    from dunders.ai.providers import _http

    h = _http._with_defaults({"Authorization": "Bearer k"})
    assert h["User-Agent"].startswith("dunders/")
    assert h["Authorization"] == "Bearer k"
    assert _http._with_defaults({"User-Agent": "x"})["User-Agent"] == "x"


async def test_openai_compat_chat_via_http(monkeypatch):
    from dunders.ai.providers import _http
    from dunders.ai.providers.openai_compat import OpenAICompatProvider

    prov = OpenAICompatProvider(
        base_url="https://api.groq.com/openai/v1", api_key="k", model="gpt-oss-120b"
    )

    def fake_post(url, headers, payload, timeout=120.0):
        assert url.endswith("/chat/completions")
        assert headers["Authorization"] == "Bearer k"
        return {"model": "gpt-oss-120b",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

    monkeypatch.setattr(_http, "post_json", fake_post)
    resp = await prov.chat(ChatRequest(messages=[user("hello")]))
    assert resp.text == "hi"
    assert resp.usage.input_tokens == 3
    assert resp.usage.output_tokens == 1


async def test_anthropic_chat_via_http(monkeypatch):
    from dunders.ai.providers import _http
    from dunders.ai.providers.anthropic import AnthropicProvider

    prov = AnthropicProvider(api_key="k", model="claude-opus-4-8")

    def fake_post(url, headers, payload, timeout=120.0):
        assert url.endswith("/v1/messages")
        assert headers["x-api-key"] == "k"
        assert "temperature" not in payload  # dropped for Opus 4.x
        return {"model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "yo"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
                "stop_reason": "end_turn"}

    monkeypatch.setattr(_http, "post_json", fake_post)
    resp = await prov.chat(
        ChatRequest(messages=[user("hi")], max_tokens=10, temperature=0.7)
    )
    assert resp.text == "yo"
    assert resp.usage.output_tokens == 2


def test_azure_builds_deployment_url():
    from dunders.ai.providers.azure import AzureOpenAIProvider

    prov = AzureOpenAIProvider(
        azure_endpoint="https://r.openai.azure.com", api_version="2024-10-21",
        deployment="gpt4o", api_key="k",
    )
    assert prov._chat_url() == (
        "https://r.openai.azure.com/openai/deployments/gpt4o"
        "/chat/completions?api-version=2024-10-21"
    )
    assert prov._headers()["api-key"] == "k"
