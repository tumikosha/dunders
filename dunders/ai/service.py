"""``LlmService`` — the single entry point every subsystem and plugin uses.

Owns the configured providers, the role→{provider, model} map, and the
guardrail stack (token meter + budget, response cache, PII redaction, no-AI
zones). Async-native; ``run_sync`` bridges worker threads to the app event loop.
Exposed on the app as ``app.ai`` and to plugins as ``api.ai``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dunders.ai.config import AiConfig, load_ai_config
from dunders.ai.guardrails import Redactor, ResponseCache, TokenMeter, is_ai_allowed
from dunders.ai.presets import PRESETS, preset_provider
from dunders.ai.provider import LlmProvider, ProviderUnavailable
from dunders.ai.providers import provider_class
from dunders.ai.secrets import SecretResolver
from dunders.ai.types import (
    ChatRequest,
    ChatResponse,
    Message,
    MessageDone,
    StreamEvent,
    Usage,
)


if TYPE_CHECKING:
    from dunders.core.plugins.events import EventBus


__all__ = ["LlmService"]


def _is_cloud(provider: LlmProvider) -> bool:
    """A provider is cloud unless it declares ``is_local = True`` (Ollama) or is
    the network-free ``fake`` provider — local models have no egress, so the
    no-AI-zone and PII guardrails don't apply to them."""
    if getattr(provider, "is_local", False):
        return False
    return provider.name != "fake"


class LlmService:
    def __init__(
        self,
        *,
        config: AiConfig | None = None,
        secrets: SecretResolver | None = None,
        events: "EventBus | None" = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.config = config or load_ai_config()
        self.secrets = secrets or SecretResolver()
        self.events = events
        self._loop = loop
        self.meter = TokenMeter(self.config.guardrails.budget_usd)
        self.cache = ResponseCache()
        self.redactor = Redactor()
        # name -> provider class, for plugin-registered providers
        self._registered: dict[str, type] = {}
        # (provider_name, model) -> built instance
        self._instances: dict[tuple[str, str], LlmProvider] = {}

    # --- lifecycle ---------------------------------------------------------

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def reload(self) -> None:
        """Re-read config from disk and drop cached provider instances."""
        self.config = load_ai_config()
        self.meter.budget_usd = self.config.guardrails.budget_usd
        self._instances.clear()

    def register_provider(self, cls: type) -> None:
        """Register a plugin-supplied provider class by its ``name``."""
        name = getattr(cls, "name", None)
        if not name:
            raise ValueError("provider class must define a 'name'")
        self._registered[name] = cls

    # --- provider resolution ----------------------------------------------

    def _build_provider(self, name: str, model: str | None) -> LlmProvider:
        cfg: dict[str, Any] = dict(self.config.providers.get(name, {}))
        if model:
            cfg["model"] = model
        if name in PRESETS:
            return preset_provider(name, cfg, self.secrets)
        cls = self._registered.get(name) or provider_class(name)
        if cls is None:
            raise ProviderUnavailable(f"Unknown AI provider: {name!r}")
        return cls.from_config(cfg, self.secrets)

    def provider_for(
        self, *, role: str = "default", provider: str | None = None,
        model: str | None = None,
    ) -> LlmProvider:
        """Resolve and (lazily) build the provider for a role or override."""
        if provider is None:
            binding = self.config.resolve_role(role)
            provider = binding.provider
            model = model or binding.model
        if not provider:
            raise ProviderUnavailable(
                f"No AI provider configured for role {role!r}. "
                "Open the '_' menu → AI / LLM settings…"
            )
        key = (provider, model or "")
        inst = self._instances.get(key)
        if inst is None:
            inst = self._build_provider(provider, model)
            self._instances[key] = inst
        return inst

    # --- guardrail helpers -------------------------------------------------

    def is_ai_allowed(self, path: str | Path | None, *, cloud: bool = True) -> bool:
        return is_ai_allowed(
            path, cloud=cloud, globs=self.config.guardrails.noai_globs
        )

    def usage_total(self) -> Usage:
        return self.meter.total()

    def reset_usage(self) -> None:
        self.meter.reset()

    def _prepare(
        self,
        provider: LlmProvider,
        req: ChatRequest,
        path: str | Path | None,
    ) -> tuple[ChatRequest, bool]:
        cloud = _is_cloud(provider)
        if cloud and not self.is_ai_allowed(path, cloud=True):
            from dunders.ai.provider import NoAiZoneError

            raise NoAiZoneError(f"AI calls are disabled for {path}")
        out = req
        if cloud and self.config.guardrails.pii_redact:
            out = self.redactor.redact_request(req)
        self.meter.check_budget()
        return out, cloud

    def _build_request(self, messages, kw: dict) -> ChatRequest:
        return ChatRequest(
            messages=list(messages),
            model=kw.get("model"),
            system=kw.get("system"),
            max_tokens=kw.get("max_tokens"),
            temperature=kw.get("temperature"),
            tools=kw.get("tools"),
            response_format=kw.get("response_format"),
            effort=kw.get("effort"),
            stream=bool(kw.get("stream", False)),
            cache=bool(kw.get("cache", self.config.guardrails.cache)),
            extra=kw.get("extra"),
        )

    # --- public API --------------------------------------------------------

    async def chat(
        self,
        messages: Sequence[Message],
        *,
        role: str = "default",
        provider: str | None = None,
        model: str | None = None,
        path: str | Path | None = None,
        **kw: Any,
    ) -> ChatResponse:
        prov = self.provider_for(role=role, provider=provider, model=model)
        kw.setdefault("model", model)
        req = self._build_request(messages, kw)
        out, _cloud = self._prepare(prov, req, path)

        use_cache = out.cache and self.config.guardrails.cache
        cache_key = self.cache.key(prov.name, out) if use_cache else None
        if cache_key is not None:
            hit = self.cache.get(cache_key)
            if hit is not None:
                return hit

        resp = await prov.chat(out)
        resp.usage = self.meter.record(resp.model or prov.name, resp.usage)
        if self.events is not None:
            self.events.emit("ai.call.done", resp)
        if cache_key is not None:
            self.cache.set(cache_key, resp)
        return resp

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        role: str = "default",
        provider: str | None = None,
        model: str | None = None,
        path: str | Path | None = None,
        **kw: Any,
    ) -> AsyncIterator[StreamEvent]:
        prov = self.provider_for(role=role, provider=provider, model=model)
        kw.setdefault("model", model)
        kw["stream"] = True
        req = self._build_request(messages, kw)
        out, _cloud = self._prepare(prov, req, path)
        async for ev in prov.stream(out):
            if isinstance(ev, MessageDone):
                ev.response.usage = self.meter.record(
                    ev.response.model or prov.name, ev.response.usage
                )
                if self.events is not None:
                    self.events.emit("ai.call.done", ev.response)
            yield ev

    # --- sync bridge -------------------------------------------------------

    def run_sync(self, coro, timeout: float | None = None):
        """Run an AI coroutine from a worker thread on the app event loop."""
        loop = self._loop
        if loop is None:
            raise ProviderUnavailable("LlmService has no event loop for run_sync")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout)

    def chat_sync(self, messages: Sequence[Message], **kw: Any) -> ChatResponse:
        return self.run_sync(self.chat(messages, **kw))
