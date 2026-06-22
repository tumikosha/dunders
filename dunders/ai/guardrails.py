"""Cross-cutting guardrails: token meter + budget, response cache, PII
redaction, and no-AI zones.

All four are pure-logic and provider-agnostic; ``LlmService`` wires them around
every call. Each is independently testable and independently toggleable.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dunders.ai.pricing import cost_of
from dunders.ai.provider import BudgetExceededError
from dunders.ai.types import (
    ChatRequest,
    ChatResponse,
    ImageBlock,
    Message,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)


__all__ = [
    "TokenMeter",
    "ResponseCache",
    "Redactor",
    "is_ai_allowed",
    "NOAI_MARKER",
]

NOAI_MARKER = ".dunders-noai"


class TokenMeter:
    """Accumulates session ``Usage`` and enforces an optional USD budget."""

    def __init__(self, budget_usd: float | None = None) -> None:
        self.budget_usd = budget_usd
        self._total = Usage()

    def total(self) -> Usage:
        return self._total

    def reset(self) -> None:
        self._total = Usage()

    def record(self, model: str, usage: Usage) -> Usage:
        """Fold ``usage`` into the running total, filling ``cost_usd`` if unset."""
        if not usage.cost_usd:
            usage = Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=cost_of(model, usage),
            )
        self._total = self._total + usage
        return usage

    def check_budget(self, projected_usd: float = 0.0) -> None:
        """Raise ``BudgetExceededError`` if spent + projected exceeds budget."""
        if self.budget_usd is None:
            return
        if self._total.cost_usd + projected_usd > self.budget_usd:
            raise BudgetExceededError(
                f"AI budget ${self.budget_usd:.2f} would be exceeded "
                f"(spent ${self._total.cost_usd:.4f})"
            )


@dataclass
class _Entry:
    response: ChatResponse
    expires_at: float


class ResponseCache:
    """In-memory TTL + LRU cache of non-streaming responses."""

    def __init__(self, maxsize: int = 256, ttl: float = 3600.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[str, _Entry] = OrderedDict()

    @staticmethod
    def key(provider: str, req: ChatRequest) -> str:
        payload = {
            "provider": provider,
            "model": req.model,
            "system": req.system,
            "messages": [_message_repr(m) for m in req.messages],
            "tools": [_tool_repr(t) for t in (req.tools or [])],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "response_format": req.response_format,
            "effort": req.effort,
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, key: str, *, now: float | None = None) -> ChatResponse | None:
        now = time.monotonic() if now is None else now
        entry = self._data.get(key)
        if entry is None:
            return None
        if entry.expires_at < now:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return entry.response

    def set(self, key: str, response: ChatResponse, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._data[key] = _Entry(response, now + self.ttl)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)


def _message_repr(m: Message) -> dict:
    if isinstance(m.content, str):
        return {"role": m.role, "content": m.content}
    blocks: list = []
    for b in m.content:
        if isinstance(b, ToolUseBlock):
            blocks.append({"tool_use": b.name, "id": b.id, "input": dict(b.input)})
        elif isinstance(b, ToolResultBlock):
            blocks.append({"tool_result": b.tool_use_id, "content": b.content})
        elif isinstance(b, ImageBlock):
            blocks.append({"image": b.media_type, "data": b.data, "url": b.url})
        else:
            blocks.append({"text": getattr(b, "text", "")})
    return {"role": m.role, "content": blocks}


def _tool_repr(t: ToolSpec) -> dict:
    return {"name": t.name, "description": t.description, "schema": dict(t.input_schema)}


# --- PII redaction ---------------------------------------------------------

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("[REDACTED_EMAIL]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("[REDACTED_KEY]", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("[REDACTED_KEY]", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("[REDACTED_KEY]", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("[REDACTED_CARD]", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    (
        "[REDACTED_PHONE]",
        re.compile(r"(?<!\d)\+?\d[\d ()-]{7,}\d(?!\d)"),
    ),
]


class Redactor:
    """Conservative regex redaction of common PII/secrets in outbound text."""

    def redact(self, text: str) -> str:
        for repl, pat in _PATTERNS:
            text = pat.sub(repl, text)
        return text

    def redact_request(self, req: ChatRequest) -> ChatRequest:
        """Return a copy of ``req`` with text content redacted."""
        new_messages = [self._redact_message(m) for m in req.messages]
        system = self.redact(req.system) if req.system else req.system
        return ChatRequest(
            messages=new_messages,
            model=req.model,
            system=system,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            tools=req.tools,
            response_format=req.response_format,
            effort=req.effort,
            stream=req.stream,
            cache=req.cache,
            extra=req.extra,
        )

    def _redact_message(self, m: Message) -> Message:
        if isinstance(m.content, str):
            return Message(role=m.role, content=self.redact(m.content))
        blocks: list = []
        for b in m.content:
            text = getattr(b, "text", None)
            if isinstance(text, str):
                from dunders.ai.types import TextBlock

                blocks.append(TextBlock(self.redact(text)))
            elif isinstance(b, ToolResultBlock):
                blocks.append(
                    ToolResultBlock(b.tool_use_id, self.redact(b.content), b.is_error)
                )
            else:
                blocks.append(b)
        return Message(role=m.role, content=blocks)


# --- no-AI zones -----------------------------------------------------------


def is_ai_allowed(
    path: str | Path | None,
    *,
    cloud: bool,
    globs: Sequence[str] = (),
) -> bool:
    """Whether an AI call touching ``path`` is permitted.

    Local (non-cloud) calls are always allowed. A cloud call is blocked when
    ``path`` matches one of ``globs`` or sits under a directory containing a
    ``.dunders-noai`` marker (searched upward to the filesystem root).
    """
    if not cloud or path is None:
        return True
    p = Path(path)
    text = str(p)
    for pattern in globs:
        if fnmatch.fnmatch(text, pattern):
            return False
    start = p if p.is_dir() else p.parent
    try:
        candidates = [start, *start.parents]
    except OSError:
        return True
    for d in candidates:
        try:
            if (d / NOAI_MARKER).exists():
                return False
        except OSError:
            continue
    return True
