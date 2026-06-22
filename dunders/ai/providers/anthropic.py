"""``AnthropicProvider`` — native Claude Messages API over stdlib HTTP.

Default model ``claude-opus-4-8``. Talks the REST API directly (``_http``) — no
``anthropic`` SDK, so ``pip install dunders`` is enough. On Opus 4.x the request
must NOT carry ``temperature``/``top_p``/``budget_tokens`` (the API 400s); this
provider omits ``temperature`` and maps ``effort`` to ``output_config``.
"""

from __future__ import annotations

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


__all__ = ["AnthropicProvider"]

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"
    capabilities = frozenset(
        {Capability.CHAT, Capability.STREAM, Capability.TOOLS,
         Capability.VISION, Capability.JSON}
    )

    def __init__(
        self, *, api_key: str | None, model: str = DEFAULT_MODEL,
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    @classmethod
    def config_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec("api_key", "API key", kind="secret",
                      default="ANTHROPIC_API_KEY",
                      help="Env var name or the key itself"),
            FieldSpec("model", "Model", default=DEFAULT_MODEL),
        ]

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], secrets: "SecretResolver"
    ) -> "AnthropicProvider":
        key_ref = str(cfg.get("api_key", "ANTHROPIC_API_KEY"))
        return cls(
            api_key=secrets.resolve(key_ref) or key_ref,
            model=str(cfg.get("model", DEFAULT_MODEL)),
            base_url=cfg.get("base_url"),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key or "",
            "anthropic-version": _API_VERSION,
        }

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(self.model, self.model, self.capabilities)]

    async def list_models(self) -> list[str]:
        import asyncio

        data = await asyncio.to_thread(
            _http.get_json, f"{self.base_url}/v1/models", self._headers()
        )
        ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
        return [i for i in ids if i]

    async def chat(self, req: ChatRequest) -> ChatResponse:
        import asyncio

        payload = self._build_payload(req, stream=False)
        data = await asyncio.to_thread(
            _http.post_json, f"{self.base_url}/v1/messages", self._headers(), payload
        )
        return _response_from(data, req.model or self.model)

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        payload = self._build_payload(req, stream=True)
        parts: list[str] = []
        model = req.model or self.model
        usage = Usage()
        stop_reason = "end_turn"
        async for raw in _http.aiter_stream_lines(
            f"{self.base_url}/v1/messages", self._headers(), payload
        ):
            line = raw.strip()
            if not line.startswith(b"data:"):
                continue
            chunk = line[len(b"data:"):].strip()
            if not chunk:
                continue
            try:
                obj = json.loads(chunk)
            except ValueError:
                continue
            etype = obj.get("type")
            if etype == "content_block_delta":
                delta = obj.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(delta["text"])
                    yield TextDelta(delta["text"])
            elif etype == "message_start":
                u = (obj.get("message") or {}).get("usage") or {}
                usage.input_tokens = int(u.get("input_tokens", 0) or 0)
            elif etype == "message_delta":
                u = obj.get("usage") or {}
                usage.output_tokens = int(u.get("output_tokens", 0) or 0)
                stop_reason = (obj.get("delta") or {}).get("stop_reason") or stop_reason
        text = "".join(parts)
        yield MessageDone(
            ChatResponse(text=text, blocks=[TextBlock(text)] if text else [],
                         model=model, stop_reason=stop_reason, usage=usage)
        )

    async def aclose(self) -> None:
        return None

    # --- mapping -----------------------------------------------------------

    def _build_payload(self, req: ChatRequest, *, stream: bool) -> dict:
        payload: dict[str, Any] = {
            "model": req.model or self.model,
            "max_tokens": req.max_tokens or 4096,
            "messages": _to_anthropic_messages(req.messages),
            "stream": stream,
        }
        if req.system:
            payload["system"] = req.system
        if req.tools:
            payload["tools"] = [_to_anthropic_tool(t) for t in req.tools]
        output_config: dict[str, Any] = {}
        if req.effort:
            output_config["effort"] = req.effort
        if req.response_format is not None:
            output_config["format"] = dict(req.response_format)
        if output_config:
            payload["output_config"] = output_config
        # NB: temperature is intentionally dropped (Opus 4.x rejects it).
        if req.extra:
            payload.update(req.extra)
        return payload


def _to_anthropic_messages(messages: Sequence[Message]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m.role == "system":
            continue  # system goes in the top-level param
        if isinstance(m.content, str):
            out.append({"role": _role(m.role), "content": m.content})
            continue
        blocks: list[dict] = []
        for b in m.content:
            if isinstance(b, TextBlock):
                blocks.append({"type": "text", "text": b.text})
            elif isinstance(b, ImageBlock):
                if b.url:
                    src = {"type": "url", "url": b.url}
                else:
                    src = {"type": "base64", "media_type": b.media_type,
                           "data": b.data or ""}
                blocks.append({"type": "image", "source": src})
            elif isinstance(b, ToolUseBlock):
                blocks.append({"type": "tool_use", "id": b.id,
                               "name": b.name, "input": dict(b.input)})
            elif isinstance(b, ToolResultBlock):
                blocks.append({"type": "tool_result", "tool_use_id": b.tool_use_id,
                               "content": b.content, "is_error": b.is_error})
        out.append({"role": _role(m.role), "content": blocks})
    return out


def _role(role: str) -> str:
    return "assistant" if role == "assistant" else "user"


def _to_anthropic_tool(t: ToolSpec) -> dict:
    return {"name": t.name, "description": t.description,
            "input_schema": dict(t.input_schema)}


def _response_from(data: Mapping[str, Any], model: str) -> ChatResponse:
    blocks: list = []
    text_parts: list[str] = []
    for b in data.get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
            blocks.append(TextBlock(b.get("text", "")))
        elif b.get("type") == "tool_use":
            blocks.append(ToolUseBlock(b.get("id", ""), b.get("name", ""),
                                       b.get("input", {}) or {}))
    u = data.get("usage") or {}
    usage = Usage(
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_read_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
    )
    return ChatResponse(
        text="".join(text_parts),
        blocks=blocks,
        model=data.get("model", model),
        stop_reason=data.get("stop_reason") or "",
        usage=usage,
        raw=data,
    )
