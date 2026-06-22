"""AiConfig: seed defaults, round-trip, and role inheritance."""

from __future__ import annotations

from dunders.ai.config import AiConfig, RoleBinding, load_ai_config, save_ai_config


def test_seed_defaults_when_empty():
    cfg = load_ai_config()
    assert cfg.resolve_role("cheap") == RoleBinding(provider="ollama", model="gemma4:e2b")
    assert cfg.resolve_role("strong") == RoleBinding(provider="groq", model="gpt-oss-120b")


def test_default_inherits_cheap():
    cfg = load_ai_config()
    resolved = cfg.resolve_role("default")
    assert resolved.provider == "ollama"
    assert resolved.model == "gemma4:e2b"


def test_roundtrip_save_load():
    cfg = AiConfig.from_dict(
        {
            "roles": {
                "strong": {"provider": "anthropic", "model": "claude-opus-4-8"},
            },
            "guardrails": {"budget_usd": 2.5, "cache": False},
        }
    )
    assert save_ai_config(cfg) is True
    again = load_ai_config()
    assert again.resolve_role("strong") == RoleBinding(
        provider="anthropic", model="claude-opus-4-8"
    )
    assert again.guardrails.budget_usd == 2.5
    assert again.guardrails.cache is False


def test_inherit_cycle_is_safe():
    cfg = AiConfig.from_dict(
        {"roles": {"default": {"inherits": "default"}}}
    )
    # Must not recurse forever; returns the (incomplete) binding.
    rb = cfg.resolve_role("default")
    assert isinstance(rb, RoleBinding)


def test_vision_unset_by_default():
    cfg = load_ai_config()
    rb = cfg.resolve_role("vision")
    assert rb.provider is None
