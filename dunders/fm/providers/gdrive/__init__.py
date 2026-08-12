"""Google Drive dunder — a ``gdrive`` VfsProvider over the Drive REST v3 API.

Split so the network-free logic is unit-testable:

- :mod:`api` — a pure Drive REST client (``DriveApi``) over an injectable
  ``HttpTransport``; a fake transport exercises list/get/download/create/delete
  without a network. ``DriveFile`` is the metadata row.
- :mod:`auth` — OAuth2 (loopback desktop flow) + refresh-token caching.
- the provider (``gdrive_provider``) maps the Drive object graph (files have
  ids, not paths) onto ``VfsPath`` name-paths, resolving name→id per level with
  a cache, exactly as the db provider maps tables→directories.

stdlib-only: every call goes over ``urllib`` like the ``dunders.ai`` layer, so
``pip install dunders`` is enough (the ``dunders[gdrive]`` extra stays empty).
"""

from dunders.fm.providers.gdrive.api import DriveApi, DriveError, DriveFile


__all__ = ["DriveApi", "DriveError", "DriveFile"]
