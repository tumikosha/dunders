"""Built-in LLM providers for ``dunders.ai``.

``FakeProvider`` is always importable (stdlib only). The real providers
(``anthropic``, ``openai_compat``, ``azure``, ``ollama``) import their vendor
SDK lazily inside ``from_config``/calls, so importing this package never
requires ``anthropic``/``openai`` to be installed.
"""

from __future__ import annotations

from dunders.ai.providers.fake import FakeProvider


__all__ = ["FakeProvider", "PROVIDER_CLASSES", "provider_class"]


def _builtin_classes() -> dict[str, type]:
    from dunders.ai.providers.anthropic import AnthropicProvider
    from dunders.ai.providers.azure import AzureOpenAIProvider
    from dunders.ai.providers.ollama import OllamaProvider
    from dunders.ai.providers.openai_compat import OpenAICompatProvider

    return {
        FakeProvider.name: FakeProvider,
        AnthropicProvider.name: AnthropicProvider,
        OpenAICompatProvider.name: OpenAICompatProvider,
        AzureOpenAIProvider.name: AzureOpenAIProvider,
        OllamaProvider.name: OllamaProvider,
    }


# Lazily populated mapping name -> provider class.
PROVIDER_CLASSES: dict[str, type] = {}


def provider_class(name: str) -> type | None:
    """Return the built-in provider class for ``name`` (loads lazily)."""
    if not PROVIDER_CLASSES:
        PROVIDER_CLASSES.update(_builtin_classes())
    return PROVIDER_CLASSES.get(name)
