"""dunders.core.vfs — virtual filesystem core.

Public surface is re-exported here; never reach into submodules from outside.
"""

from dunders.core.vfs.locator import VfsPath
from dunders.core.vfs.registry import VfsRegistry


__all__ = ["VfsPath", "VfsRegistry"]
