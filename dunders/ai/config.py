"""AI config: the ``ai`` section of ``$XDG_CONFIG_HOME/dunders/config.json``.

Stores the role→{provider, model} map, per-provider non-secret fields, and the
guardrail toggles. Secrets never live here (see ``secrets.py``). Built on the
same stdlib-JSON ``user_config`` helpers; missing keys fall back to the seed
defaults below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dunders.config.user_config import load_config, save_config


__all__ = [
    "RoleBinding",
    "AiConfig",
    "GuardrailConfig",
    "ROLES",
    "load_ai_config",
    "save_ai_config",
]

# The five roles a consumer can request, in display order.
ROLES = ("default", "cheap", "strong", "local", "vision")

# Seed defaults applied when nothing is configured yet.
_SEED_ROLES: dict[str, dict[str, str]] = {
    "default": {"inherits": "cheap"},
    "cheap": {"provider": "ollama", "model": "gemma4:e2b"},
    "strong": {"provider": "groq", "model": "gpt-oss-120b"},
    "local": {"provider": "ollama", "model": "gemma4:e2b"},
    "vision": {},
}


@dataclass(slots=True)
class RoleBinding:
    provider: str | None = None
    model: str | None = None
    inherits: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "RoleBinding":
        return cls(
            provider=d.get("provider"),
            model=d.get("model"),
            inherits=d.get("inherits"),
        )

    def to_dict(self) -> dict:
        out: dict[str, str] = {}
        if self.provider:
            out["provider"] = self.provider
        if self.model:
            out["model"] = self.model
        if self.inherits:
            out["inherits"] = self.inherits
        return out


@dataclass(slots=True)
class GuardrailConfig:
    budget_usd: float | None = None
    cache: bool = True
    pii_redact: bool = True
    noai_globs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "GuardrailConfig":
        budget = d.get("budget_usd")
        globs = d.get("noai_globs")
        return cls(
            budget_usd=float(budget) if isinstance(budget, (int, float)) else None,
            cache=bool(d.get("cache", True)),
            pii_redact=bool(d.get("pii_redact", True)),
            noai_globs=[g for g in globs if isinstance(g, str)]
            if isinstance(globs, list)
            else [],
        )

    def to_dict(self) -> dict:
        return {
            "budget_usd": self.budget_usd,
            "cache": self.cache,
            "pii_redact": self.pii_redact,
            "noai_globs": list(self.noai_globs),
        }


@dataclass(slots=True)
class AiConfig:
    roles: dict[str, RoleBinding] = field(default_factory=dict)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "AiConfig":
        raw_roles = d.get("roles") if isinstance(d.get("roles"), dict) else {}
        roles: dict[str, RoleBinding] = {}
        for name in ROLES:
            src = raw_roles.get(name)
            if isinstance(src, dict):
                roles[name] = RoleBinding.from_dict(src)
            else:
                roles[name] = RoleBinding.from_dict(_SEED_ROLES.get(name, {}))
        providers = d.get("providers")
        guardrails = d.get("guardrails")
        return cls(
            roles=roles,
            providers={
                k: v for k, v in providers.items() if isinstance(v, dict)
            }
            if isinstance(providers, dict)
            else {},
            guardrails=GuardrailConfig.from_dict(
                guardrails if isinstance(guardrails, dict) else {}
            ),
        )

    def to_dict(self) -> dict:
        return {
            "roles": {name: rb.to_dict() for name, rb in self.roles.items()},
            "providers": dict(self.providers),
            "guardrails": self.guardrails.to_dict(),
        }

    def resolve_role(self, role: str, _seen: frozenset[str] = frozenset()) -> RoleBinding:
        """Follow ``inherits`` chains to a concrete (provider, model) binding."""
        rb = self.roles.get(role)
        if rb is None:
            return RoleBinding()
        if rb.provider and rb.model:
            return rb
        if rb.inherits and rb.inherits not in _seen and rb.inherits != role:
            return self.resolve_role(rb.inherits, _seen | {role})
        return rb


def load_ai_config() -> AiConfig:
    """Load the ``ai`` section, applying seed defaults for anything unset."""
    return AiConfig.from_dict(load_config().get("ai", {}) or {})


def save_ai_config(cfg: AiConfig) -> bool:
    """Persist ``cfg`` into the ``ai`` section, preserving other top-level keys."""
    data = load_config()
    data["ai"] = cfg.to_dict()
    return save_config(data)
