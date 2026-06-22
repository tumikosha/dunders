"""dunders.sdk — the stable public API for writing dunder plugins.

This is the ONLY module a plugin author should import from. Everything here is
a deliberate, supported surface; the rest of ``dunders.*`` is private and may
change between releases.

A minimal plugin::

    from dunders.sdk import DunderPlugin, PluginApi, VfsProvider

    class MyProvider:
        scheme = "myfs"
        capabilities = frozenset({"read"})
        def scan(self, loc, *, show_hidden=False, include_parent=True): ...
        # ... rest of the VfsProvider contract ...

    class MyPlugin:
        name = "my-fs"
        version = "0.1.0"
        def register(self, api: PluginApi) -> None:
            api.vfs.register(MyProvider())
            api.events.on("op.copy.done", lambda result: ...)

    plugin = MyPlugin()   # module-level object discovery looks for
"""

from dunders.ai import (
    AiError,
    Capability,
    ChatRequest,
    ChatResponse,
    FieldSpec,
    ImageBlock,
    LlmProvider,
    LlmService,
    Message,
    ModelInfo,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from dunders.core.plugins.api import PluginApi
from dunders.core.plugins.events import EventBus
from dunders.core.plugins.plugin import DunderPlugin
from dunders.core.vfs import VfsPath, VfsRegistry
from dunders.core.vfs.provider import TargetResolver, VfsProvider
from dunders.fm.actions import OpError, OpResult
from dunders.fm.file_entry import FileEntry
from dunders.windowing.content import WindowCommand, WindowContent


__all__ = [
    # Plugin contract
    "DunderPlugin",
    "PluginApi",
    "EventBus",
    # VFS
    "VfsPath",
    "VfsProvider",
    "VfsRegistry",
    "TargetResolver",
    "FileEntry",
    "OpResult",
    "OpError",
    # Windowing (for content/command plugins)
    "WindowContent",
    "WindowCommand",
    # AI / LLM foundation
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
    "AiError",
]
