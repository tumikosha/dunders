"""Backend factory. Subprocess and pty implementations live in sibling
modules; pty is POSIX-only and degrades gracefully on Windows.
"""

from __future__ import annotations

from typing import Callable

from .base import Backend


_FACTORIES: dict[str, Callable[[], Backend]] = {}


def register(name: str, factory: Callable[[], Backend]) -> None:
    _FACTORIES[name] = factory


def make_backend(name: str) -> Backend:
    if name not in _FACTORIES:
        raise ValueError(
            f"Unknown backend {name!r}. Available: {sorted(_FACTORIES)}"
        )
    return _FACTORIES[name]()


def available() -> list[str]:
    return sorted(_FACTORIES)
