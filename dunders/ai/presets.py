"""Vendor presets over ``OpenAICompatProvider``.

groq / Nvidia NIM / DeepSeek / Qwen are all OpenAI-API-compatible, so each is
just a ``base_url`` + env-key + default model over the one shared class — not a
separate provider implementation. The wizard offers these as ready-made
provider choices; selecting one pre-fills the OpenAI-compat fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dunders.ai.providers.openai_compat import OpenAICompatProvider


if TYPE_CHECKING:
    from dunders.ai.secrets import SecretResolver


__all__ = ["PRESETS", "preset_provider", "preset_names"]

# name -> (base_url, env_key, default_model)
PRESETS: dict[str, tuple[str, str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "gpt-oss-120b"),
    "nvidia": (
        "https://integrate.api.nvidia.com/v1",
        "NVIDIA_API_KEY",
        "meta/llama-3.1-70b-instruct",
    ),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    "qwen": (
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "DASHSCOPE_API_KEY",
        "qwen-plus",
    ),
}


def preset_names() -> list[str]:
    return list(PRESETS)


def preset_provider(
    name: str, cfg: Mapping[str, Any], secrets: "SecretResolver"
) -> OpenAICompatProvider:
    """Build an ``OpenAICompatProvider`` for preset ``name``, overlaying ``cfg``."""
    base_url, env_key, default_model = PRESETS[name]
    key_ref = str(cfg.get("api_key", env_key))
    return OpenAICompatProvider(
        base_url=str(cfg.get("base_url", base_url)),
        api_key=secrets.resolve(key_ref) or key_ref,
        model=str(cfg.get("model", default_model)),
        vision=bool(cfg.get("vision", False)),
        name=name,
    )
