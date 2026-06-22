"""Core data types for the ``dunders.ai`` LLM foundation.

Provider-agnostic value objects shared by every provider and the ``LlmService``.
Pure dataclasses + stdlib only — importing this module must never require an
LLM SDK (``anthropic``/``openai``). Providers translate these to/from their
vendor SDKs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


__all__ = [
    "Role",
    "TextBlock",
    "ImageBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ContentBlock",
    "Message",
    "ToolSpec",
    "ChatRequest",
    "Usage",
    "ChatResponse",
    "TextDelta",
    "ToolUseDelta",
    "MessageDone",
    "StreamEvent",
    "FieldSpec",
    "ModelInfo",
    "Capability",
    "user",
    "system_msg",
    "assistant",
]

Role = Literal["system", "user", "assistant", "tool"]


class Capability:
    """String constants for ``LlmProvider.capabilities`` (a ``frozenset``)."""

    CHAT = "chat"
    STREAM = "stream"
    TOOLS = "tools"
    VISION = "vision"
    JSON = "json"

    ALL = frozenset({CHAT, STREAM, TOOLS, VISION, JSON})


@dataclass(slots=True)
class TextBlock:
    text: str


@dataclass(slots=True)
class ImageBlock:
    """An image input. Exactly one of ``data`` (base64) or ``url`` is set."""

    media_type: str = "image/png"
    data: str | None = None  # base64-encoded bytes
    url: str | None = None


@dataclass(slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: Mapping[str, Any]


@dataclass(slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock


@dataclass(slots=True)
class Message:
    role: Role
    content: str | list[ContentBlock]

    def text(self) -> str:
        """Concatenated text of this message (ignores non-text blocks)."""
        if isinstance(self.content, str):
            return self.content
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(slots=True)
class ChatRequest:
    """A normalized chat request. Every tuning knob is optional; a provider
    consumes what its ``capabilities`` allow and silently drops the rest — it
    must never surface a vendor 400 for an unsupported parameter."""

    messages: Sequence[Message]
    model: str | None = None
    system: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    tools: Sequence[ToolSpec] | None = None
    response_format: Mapping[str, Any] | None = None  # JSON schema for structured out
    effort: str | None = None  # low|medium|high|xhigh|max (Anthropic-style)
    stream: bool = False
    cache: bool = True
    extra: Mapping[str, Any] | None = None


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


@dataclass(slots=True)
class ChatResponse:
    text: str
    blocks: list[ContentBlock] = field(default_factory=list)
    model: str = ""
    stop_reason: str = ""
    usage: Usage = field(default_factory=Usage)
    raw: Any = None


# --- streaming events ------------------------------------------------------


@dataclass(slots=True)
class TextDelta:
    text: str


@dataclass(slots=True)
class ToolUseDelta:
    id: str
    name: str
    partial_json: str = ""


@dataclass(slots=True)
class MessageDone:
    response: ChatResponse


StreamEvent = TextDelta | ToolUseDelta | MessageDone


# --- wizard / config schema ------------------------------------------------


@dataclass(slots=True)
class FieldSpec:
    """One configurable field of a provider, used to build the wizard form."""

    name: str
    label: str
    kind: Literal["text", "secret", "url", "int", "choice"] = "text"
    required: bool = True
    default: Any = None
    help: str = ""
    choices: Sequence[str] | None = None


@dataclass(slots=True)
class ModelInfo:
    id: str
    label: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)


# --- convenience message constructors -------------------------------------


def user(text: str) -> Message:
    return Message(role="user", content=text)


def system_msg(text: str) -> Message:
    return Message(role="system", content=text)


def assistant(text: str) -> Message:
    return Message(role="assistant", content=text)
