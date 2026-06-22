"""``OpenAICompatProvider`` — one implementation for every OpenAI-compatible
endpoint (OpenAI, groq, Nvidia NIM, DeepSeek, Qwen/DashScope, …).

Parameterized by ``base_url`` + an api-key (resolved from a secret name) +
``model``. Talks the REST API directly over stdlib HTTP (``_http``) — **no
``openai`` SDK**, so ``pip install dunders`` is enough. The ``presets.py`` table
turns each vendor into a pre-filled config over this class — one class, many
vendors. ``AzureOpenAIProvider`` subclasses it and overrides the URL/auth.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from dunders.ai.providers import _http
from dunders.ai.types import (
    Capability,
    ChatRequest,
    ChatResponse,
    FieldSpec,
    ImageBlock,
    Message,
    MessageDone,
    ModelInfo,
    StreamEvent,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)


if TYPE_CHECKING:
    from dunders.ai.secrets import SecretResolver


__all__ = ["OpenAICompatProvider"]


class OpenAICompatProvider:
    name = "openai"
    capabilities = frozenset(
        {Capability.CHAT, Capability.STREAM, Capability.TOOLS, Capability.JSON}
    )

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str,
        vision: bool = False,
        name: str | None = None,
    ) -> None:
        if name:
            self.name = name
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = model
        self._api_key = api_key
        if vision:
            self.capabilities = self.capabilities | {Capability.VISION}

    @classmethod
    def config_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec("base_url", "Base URL", kind="url",
                      default="https://api.openai.com/v1"),
            FieldSpec("api_key", "API key", kind="secret",
                      default="OPENAI_API_KEY",
                      help="Env var name or the key itself"),
            FieldSpec("model", "Model", default="gpt-4o-mini"),
        ]

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], secrets: "SecretResolver"
    ) -> "OpenAICompatProvider":
        key_ref = str(cfg.get("api_key", "OPENAI_API_KEY"))
        api_key = secrets.resolve(key_ref) or key_ref
        return cls(
            base_url=cfg.get("base_url"),
            api_key=api_key,
            model=str(cfg.get("model", "gpt-4o-mini")),
            vision=bool(cfg.get("vision", False)),
            name=cfg.get("provider_name"),
        )

    # --- endpoint shape (overridden by Azure) ------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key or ''}"}

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _models_url(self) -> str:
        return f"{self.base_url}/models"

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(self.model, self.model, self.capabilities)]

    async def list_models(self) -> list[str]:
        data = await asyncio.to_thread(_http.get_json, self._models_url(), self._headers())
        ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
        return sorted(i for i in ids if i)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        payload = self._build_payload(req, stream=False)
        data = await asyncio.to_thread(
            _http.post_json, self._chat_url(), self._headers(), payload
        )
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        blocks: list = [TextBlock(text)] if text else []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            blocks.append(ToolUseBlock(tc.get("id", ""), fn.get("name", ""), args))
        return ChatResponse(
            text=text,
            blocks=blocks,
            model=data.get("model", req.model or self.model),
            stop_reason=choice.get("finish_reason") or "",
            usage=_usage_from(data),
            raw=data,
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(req, stream=True)
        payload["stream_options"] = {"include_usage": True}
        parts: list[str] = []
        usage = Usage()
        model = req.model or self.model
        async for raw in _http.aiter_stream_lines(
            self._chat_url(), self._headers(), payload
        ):
            line = raw.strip()
            if not line.startswith(b"data:"):
                continue
            chunk = line[len(b"data:"):].strip()
            if chunk in (b"[DONE]", b""):
                continue
            try:
                obj = json.loads(chunk)
            except ValueError:
                continue
            if obj.get("usage"):
                usage = _usage_from(obj)
            for ch in obj.get("choices") or []:
                delta = (ch.get("delta") or {}).get("content")
                if delta:
                    parts.append(delta)
                    yield TextDelta(delta)
        text = "".join(parts)
        yield MessageDone(
            ChatResponse(text=text, blocks=[TextBlock(text)] if text else [],
                         model=model, stop_reason="end_turn", usage=usage)
        )

    async def aclose(self) -> None:
        return None

    # --- mapping -----------------------------------------------------------

    def _build_payload(self, req: ChatRequest, *, stream: bool) -> dict:
        payload: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": _to_openai_messages(req.messages, req.system),
            "stream": stream,
        }
        if req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.tools:
            payload["tools"] = [_to_openai_tool(t) for t in req.tools]
        if req.response_format is not None:
            payload["response_format"] = dict(req.response_format)
        if req.extra:
            payload.update(req.extra)
        return payload


def _to_openai_messages(
    messages: Sequence[Message], system: str | None
) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        if isinstance(m.content, str):
            out.append({"role": m.role, "content": m.content})
            continue
        tool_calls = []
        parts: list[dict] = []
        emitted_tool_results = False
        for b in m.content:
            if isinstance(b, TextBlock):
                parts.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                url = b.url or f"data:{b.media_type};base64,{b.data}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            elif isinstance(b, ToolUseBlock):
                tool_calls.append({
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name, "arguments": json.dumps(dict(b.input))},
                })
            elif isinstance(b, ToolResultBlock):
                out.append({
                    "role": "tool",
                    "tool_call_id": b.tool_use_id,
                    "content": b.content,
                })
                emitted_tool_results = True
        if parts or tool_calls:
            msg: dict[str, Any] = {"role": m.role}
            if parts:
                msg["content"] = parts
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        elif not emitted_tool_results:
            out.append({"role": m.role, "content": ""})
    return out


def _to_openai_tool(t: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": dict(t.input_schema),
        },
    }


def _usage_from(data: Mapping[str, Any]) -> Usage:
    u = data.get("usage") or {}
    return Usage(
        input_tokens=int(u.get("prompt_tokens", 0) or 0),
        output_tokens=int(u.get("completion_tokens", 0) or 0),
    )
