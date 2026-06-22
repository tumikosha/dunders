"""``LlmProvider`` — the contract every LLM backend implements.

Modeled on ``VfsProvider``: a ``capabilities`` frozenset declares what the
provider supports, and a small async method surface (``chat``/``stream``) plus a
``config_schema``/``from_config`` pair so the wizard can build a form and
construct the provider generically. All vendor SDK exceptions are mapped to the
``AiError`` hierarchy below so callers never see provider-specific errors.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from dunders.ai.types import (
    ChatRequest,
    ChatResponse,
    FieldSpec,
    ModelInfo,
    StreamEvent,
)


if TYPE_CHECKING:
    from dunders.ai.secrets import SecretResolver


__all__ = [
    "LlmProvider",
    "AiError",
    "AuthError",
    "RateLimitError",
    "AiTimeoutError",
    "ProviderUnavailable",
    "BudgetExceededError",
    "NoAiZoneError",
    "CapabilityError",
]


class AiError(Exception):
    """Base class for every error surfaced by the AI layer."""


class AuthError(AiError):
    """Missing/invalid credentials."""


class RateLimitError(AiError):
    """Provider rate limit hit (HTTP 429 or equivalent)."""


class AiTimeoutError(AiError):
    """The request timed out."""


class ProviderUnavailable(AiError):
    """Provider not configured, SDK missing, or endpoint unreachable."""


class BudgetExceededError(AiError):
    """The session token/cost budget would be exceeded by this call."""


class NoAiZoneError(AiError):
    """A cloud call was attempted against a path in a no-AI zone."""


class CapabilityError(AiError):
    """A capability was requested that the selected provider does not support."""


@runtime_checkable
class LlmProvider(Protocol):
    name: str
    capabilities: frozenset[str]

    @classmethod
    def config_schema(cls) -> list[FieldSpec]:
        """Fields the wizard renders to configure this provider."""
        ...

    @classmethod
    def from_config(
        cls, cfg: Mapping[str, Any], secrets: "SecretResolver"
    ) -> "LlmProvider":
        """Build a provider from a saved config dict + a secret resolver."""
        ...

    def models(self) -> list[ModelInfo]:
        """Known models (static list or a cached fetch). May be empty."""
        ...

    async def list_models(self) -> list[str]:
        """Fetch the live list of available model ids from the provider's
        models endpoint. May raise an ``AiError`` (auth/network)."""
        ...

    async def chat(self, req: ChatRequest) -> ChatResponse:
        """One non-streaming completion."""
        ...

    def stream(self, req: ChatRequest) -> AsyncIterator[StreamEvent]:
        """Stream a completion as ``StreamEvent``s, ending with ``MessageDone``."""
        ...

    async def aclose(self) -> None:
        """Release any underlying client/connection."""
        ...
