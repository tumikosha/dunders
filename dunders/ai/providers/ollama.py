"""``OllamaProvider`` — a local Ollama server over its **native HTTP API**.

Ollama is the default ``cheap``/``local`` provider, local and free, so it must
work with **no extra dependencies** — it talks to Ollama's REST API
(``/api/tags``, ``/api/chat``) with stdlib ``urllib`` (run off the event loop via
``asyncio.to_thread``), not the ``openai`` SDK. Marks itself ``is_local`` so the
no-AI-zone and PII guardrails treat it as local (no cloud egress).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from dunders.ai.provider import (
    AiError,
    AiTimeoutError,
    AuthError,
    ProviderUnavailable,
)
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
    Usage,
)


if TYPE_CHECKING:
    from dunders.ai.secrets import SecretResolver


__all__ = ["OllamaProvider"]

DEFAULT_BASE_URL = "http://localhost:11434/v1"
_TIMEOUT = 120.0


class OllamaProvider:
    name = "ollama"
    is_local = True  # consulted by the guardrails (no cloud egress)
    capabilities = frozenset({Capability.CHAT, Capability.STREAM})

    def __init__(self, *, base_url: str | None, model: str, vision: bool = False) -> None:
        self.base_url = base_url or DEFAULT_BASE_URL
        self.model = model
        if vision:
            self.capabilities = self.capabilities | {Capability.VISION}

    @classmethod
    def config_schema(cls) -> list[FieldSpec]:
        return [
            FieldSpec("base_url", "Base URL", kind="url", default=DEFAULT_BASE_URL),
            FieldSpec("model", "Model", default="gemma4:e2b"),
        ]

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], secrets: "SecretResolver"
    ) -> "OllamaProvider":
        return cls(
            base_url=cfg.get("base_url"),
            model=str(cfg.get("model", "gemma4:e2b")),
            vision=bool(cfg.get("vision", False)),
        )

    # --- native endpoint helpers ------------------------------------------

    def _native_base(self) -> str:
        """Ollama's REST root — the OpenAI-compat ``/v1`` suffix stripped off."""
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base

    def _request(self, path: str, payload: dict | None = None) -> bytes:
        url = self._native_base() + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url, data=data, method="POST" if data is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError(f"Ollama auth error ({exc.code})") from exc
            if exc.code == 404:
                raise ProviderUnavailable(
                    "Ollama endpoint or model not found (404). "
                    "Is the model pulled? `ollama pull <model>`"
                ) from exc
            raise AiError(f"Ollama HTTP {exc.code}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AiTimeoutError("Ollama request timed out") from exc
        except urllib.error.URLError as exc:
            raise ProviderUnavailable(
                f"Cannot reach Ollama at {self._native_base()} — is it running? "
                "(`ollama serve`)"
            ) from exc

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(self.model, self.model, self.capabilities)]

    async def list_models(self) -> list[str]:
        raw = await asyncio.to_thread(self._request, "/api/tags")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise AiError("Ollama returned malformed JSON") from exc
        names = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
        return sorted(n for n in names if n)

    async def chat(self, req: ChatRequest) -> ChatResponse:
        payload = self._build_payload(req, stream=False)
        raw = await asyncio.to_thread(self._request, "/api/chat", payload)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise AiError("Ollama returned malformed JSON") from exc
        text = (data.get("message") or {}).get("content", "") or ""
        usage = Usage(
            input_tokens=int(data.get("prompt_eval_count", 0) or 0),
            output_tokens=int(data.get("eval_count", 0) or 0),
        )
        return ChatResponse(
            text=text,
            blocks=[TextBlock(text)] if text else [],
            model=data.get("model", req.model or self.model),
            stop_reason="end_turn" if data.get("done") else "",
            usage=usage,
            raw=data,
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        # Native streaming is newline-delimited JSON; reading the socket
        # incrementally from a worker thread keeps this dependency-free while
        # still surfacing token deltas.
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        payload = self._build_payload(req, stream=True)

        def _pump() -> None:
            try:
                url = self._native_base() + "/api/chat"
                data = json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    url, data=data, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
                    for line in resp:
                        line = line.strip()
                        if line:
                            loop.call_soon_threadsafe(queue.put_nowait, ("chunk", line))
                loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
            except Exception as exc:  # noqa: BLE001 - marshalled to the consumer
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

        task = asyncio.create_task(asyncio.to_thread(_pump))
        parts: list[str] = []
        usage = Usage()
        model = req.model or self.model
        try:
            while True:
                kind, value = await queue.get()
                if kind == "chunk":
                    try:
                        obj = json.loads(value)
                    except ValueError:
                        continue
                    delta = (obj.get("message") or {}).get("content", "")
                    if delta:
                        parts.append(delta)
                        yield TextDelta(delta)
                    if obj.get("done"):
                        usage = Usage(
                            input_tokens=int(obj.get("prompt_eval_count", 0) or 0),
                            output_tokens=int(obj.get("eval_count", 0) or 0),
                        )
                elif kind == "error":
                    raise self._map_pump_error(value)
                else:  # done
                    break
        finally:
            task.cancel()
        text = "".join(parts)
        yield MessageDone(
            ChatResponse(text=text, blocks=[TextBlock(text)] if text else [],
                         model=model, stop_reason="end_turn", usage=usage)
        )

    @staticmethod
    def _map_pump_error(exc: Exception) -> AiError:
        if isinstance(exc, AiError):
            return exc
        if isinstance(exc, urllib.error.URLError):
            return ProviderUnavailable("Cannot reach Ollama — is it running?")
        return AiError(str(exc))

    async def aclose(self) -> None:
        return None

    # --- mapping -----------------------------------------------------------

    def _build_payload(self, req: ChatRequest, *, stream: bool) -> dict:
        messages: list[dict] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        for m in req.messages:
            messages.append(_to_ollama_message(m))
        payload: dict[str, Any] = {
            "model": req.model or self.model,
            "messages": messages,
            "stream": stream,
        }
        options: dict[str, Any] = {}
        if req.max_tokens is not None:
            options["num_predict"] = req.max_tokens
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if options:
            payload["options"] = options
        return payload


def _to_ollama_message(m: Message) -> dict:
    if isinstance(m.content, str):
        return {"role": _role(m.role), "content": m.content}
    text_parts: list[str] = []
    images: list[str] = []
    for b in m.content:
        if isinstance(b, TextBlock):
            text_parts.append(b.text)
        elif isinstance(b, ToolResultBlock):
            text_parts.append(b.content)
        elif isinstance(b, ImageBlock) and b.data:
            images.append(b.data)  # native API takes base64 images here
    msg: dict[str, Any] = {"role": _role(m.role), "content": "".join(text_parts)}
    if images:
        msg["images"] = images
    return msg


def _role(role: str) -> str:
    return "assistant" if role == "assistant" else (
        "system" if role == "system" else "user"
    )
