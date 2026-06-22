"""``FakeProvider`` — a deterministic, network-free provider.

The workhorse of the test suite and the fallback when nothing is configured.
``chat`` returns a canned (or echoed) response with a synthetic ``Usage``;
``stream`` chunks that text into ``TextDelta``s followed by a ``MessageDone``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any

from dunders.ai.types import (
    ChatRequest,
    ChatResponse,
    Capability,
    FieldSpec,
    MessageDone,
    ModelInfo,
    StreamEvent,
    TextDelta,
    Usage,
)


if TYPE_CHECKING:
    from dunders.ai.secrets import SecretResolver


__all__ = ["FakeProvider"]


class FakeProvider:
    name = "fake"
    capabilities = Capability.ALL

    def __init__(
        self,
        *,
        reply: str | None = None,
        scripted: list[str] | None = None,
        model: str = "fake-1",
    ) -> None:
        self._reply = reply
        self._scripted = list(scripted or [])
        self._model = model
        self.calls: list[ChatRequest] = []

    # --- contract ----------------------------------------------------------

    @classmethod
    def config_schema(cls) -> list[FieldSpec]:
        return [FieldSpec("model", "Model", default="fake-1", required=False)]

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], secrets: "SecretResolver"
    ) -> "FakeProvider":
        return cls(model=str(cfg.get("model", "fake-1")))

    def models(self) -> list[ModelInfo]:
        return [ModelInfo(self._model, "Fake model", Capability.ALL)]

    async def list_models(self) -> list[str]:
        return [self._model, "fake-mini", "fake-large"]

    def _next_text(self, req: ChatRequest) -> str:
        if self._scripted:
            return self._scripted.pop(0)
        if self._reply is not None:
            return self._reply
        # Echo the last user message text.
        for m in reversed(list(req.messages)):
            if m.role == "user":
                return f"echo: {m.text()}"
        return ""

    async def chat(self, req: ChatRequest) -> ChatResponse:
        self.calls.append(req)
        text = self._next_text(req)
        usage = Usage(input_tokens=_estimate_tokens(req), output_tokens=len(text) // 4)
        return ChatResponse(
            text=text,
            model=req.model or self._model,
            stop_reason="end_turn",
            usage=usage,
        )

    async def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        resp = await self.chat(req)
        words = resp.text.split(" ")
        for i, w in enumerate(words):
            yield TextDelta(w if i == 0 else " " + w)
        yield MessageDone(resp)

    async def aclose(self) -> None:  # nothing to release
        return None


def _estimate_tokens(req: ChatRequest) -> int:
    chars = sum(len(m.text()) for m in req.messages) + len(req.system or "")
    return max(1, chars // 4)
