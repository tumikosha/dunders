"""``dunders.ai`` — the cross-cutting LLM foundation.

App-agnostic: depends only on stdlib + optional vendor SDKs (imported lazily by
each provider). Public surface is re-exported through ``dunders.sdk``; the
``dunders.ai.*`` modules themselves are private to the app and may change.

The single runtime object is :class:`LlmService` (``app.ai`` / ``api.ai``),
which raises every subsystem and plugin to one configured set of providers
behind a role map (``default``/``cheap``/``strong``/``local``/``vision``).
"""

from __future__ import annotations

from dunders.ai.provider import (
    AiError,
    AiTimeoutError,
    AuthError,
    BudgetExceededError,
    CapabilityError,
    LlmProvider,
    NoAiZoneError,
    ProviderUnavailable,
    RateLimitError,
)
from dunders.ai.service import LlmService
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
    assistant,
    system_msg,
    user,
)


__all__ = [
    "LlmService",
    "LlmProvider",
    "ChatRequest",
    "ChatResponse",
    "Message",
    "TextBlock",
    "ImageBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ToolSpec",
    "FieldSpec",
    "ModelInfo",
    "Capability",
    "Usage",
    "StreamEvent",
    "TextDelta",
    "MessageDone",
    "user",
    "assistant",
    "system_msg",
    "AiError",
    "AuthError",
    "RateLimitError",
    "AiTimeoutError",
    "ProviderUnavailable",
    "BudgetExceededError",
    "NoAiZoneError",
    "CapabilityError",
]
