"""PluginApi — the narrow surface a dunder plugin is handed at registration.

A plugin's ``register(api)`` extends the app *only* through this object, never
by reaching into ``dunders.app`` / ``dunders.windowing`` directly. Keeping the
surface narrow lets the core refactor freely behind a stable contract.

v1 exposes:
- ``api.vfs``    — the shared :class:`VfsRegistry`; ``api.vfs.register(provider)``
                   adds a filesystem scheme (archives, remote, API, …).
- ``api.events`` — the :class:`EventBus`; ``api.events.on("op.copy.done", fn)``.
- ``api.ai``     — the shared :class:`LlmService`; ``await api.ai.chat(...)`` /
                   ``api.ai.register_provider(MyLlmProvider)``. ``None`` when the
                   AI layer isn't wired (e.g. some test harnesses).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dunders.ai import LlmService
    from dunders.core.plugins.events import EventBus
    from dunders.core.vfs import VfsRegistry


__all__ = ["PluginApi"]


class PluginApi:
    def __init__(
        self,
        *,
        vfs: VfsRegistry,
        events: EventBus,
        ai: "LlmService | None" = None,
    ) -> None:
        self.vfs = vfs
        self.events = events
        self.ai = ai

    def register_provider(self, provider_cls: type) -> None:
        """Register a plugin-supplied :class:`LlmProvider` class.

        No-op when the AI layer isn't wired (``api.ai`` is ``None``).
        """
        if self.ai is not None:
            self.ai.register_provider(provider_cls)
