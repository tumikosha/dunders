"""FormsService: the runtime object behind ``app.forms`` (mirrors ``app.ai``).

``ask`` opens a :class:`FormDialog` and returns an awaitable Future that
resolves to the typed result dict (or None on cancel) via the app's dialog
handlers.  Calling ``ask`` is synchronous — it opens the dialog immediately
and the returned Future is resolved by the Submitted/Cancelled handlers.
"""

from __future__ import annotations

import asyncio

from dunders.forms import FormSpec, parse_schema


class FormsService:
    def __init__(self, app) -> None:
        self._app = app

    def ask(
        self,
        spec: "FormSpec | dict",
        *,
        selected_text: str | None = None,
    ) -> "asyncio.Future[dict | None]":
        """Open a FormDialog and return a Future that resolves on GO/Cancel.

        The Future resolves to a typed result dict when the user submits, or
        None when they cancel.  The caller should ``await`` the returned Future
        to get the result::

            result = await app.forms.ask(spec)
        """
        if isinstance(spec, dict):
            spec = parse_schema(spec)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict | None] = loop.create_future()

        def _done(result: "dict | None") -> None:
            if not fut.done():
                fut.set_result(result)

        self._app._open_form(
            spec, selected_text=selected_text or "", on_result=_done
        )
        return fut
