"""Built-in VFS providers beyond the local filesystem.

Each module here implements ``dunders.core.vfs.provider.VfsProvider`` for one
scheme and is wired into ``default_registry()``. When the plugin SDK lands,
these graduate into external "dunders" without changing their provider logic.
"""
