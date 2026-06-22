"""Tiny stdlib HTTP helpers shared by the providers.

Every provider talks to its REST endpoint directly over ``urllib`` (blocking
calls pushed off the event loop with ``asyncio.to_thread``), so the AI layer
needs **no** vendor SDK — ``pip install dunders`` is enough. Errors map to the
``AiError`` hierarchy; ``aiter_stream_lines`` turns a streaming POST into an
async iterator of raw lines (SSE / NDJSON parsing is the caller's job).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from dunders.ai.provider import (
    AiError,
    AiTimeoutError,
    AuthError,
    ProviderUnavailable,
    RateLimitError,
)


__all__ = [
    "DEFAULT_TIMEOUT",
    "post_json",
    "get_json",
    "aiter_stream_lines",
    "map_status",
]

DEFAULT_TIMEOUT = 120.0
# A real User-Agent: the stdlib default ("Python-urllib/3.x") is banned by
# Cloudflare in front of some APIs (e.g. groq → HTTP 403 "error code: 1010").
_USER_AGENT = "dunders/0.1 (+https://github.com/tumikosha/dunders)"


def _with_defaults(headers: Mapping[str, str]) -> dict[str, str]:
    return {"User-Agent": _USER_AGENT, **headers}


def map_status(code: int, reason: str, detail: str = "") -> AiError:
    snippet = f" — {detail[:200]}" if detail else ""
    if code in (401, 403):
        return AuthError(f"Authentication failed ({code}){snippet}")
    if code == 429:
        return RateLimitError(f"Rate limited (429){snippet}")
    if code == 404:
        return ProviderUnavailable(f"Endpoint or model not found (404){snippet}")
    return AiError(f"HTTP {code} {reason}{snippet}")


def _request_bytes(
    method: str, url: str, headers: Mapping[str, str],
    body: bytes | None, timeout: float,
) -> bytes:
    req = urllib.request.Request(
        url, data=body, method=method, headers=_with_defaults(headers)
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        raise map_status(exc.code, str(getattr(exc, "reason", "")), detail) from exc
    except TimeoutError as exc:
        raise AiTimeoutError(f"Request to {url} timed out") from exc
    except urllib.error.URLError as exc:
        raise ProviderUnavailable(f"Cannot reach {url}: {exc.reason}") from exc


def post_json(
    url: str, headers: Mapping[str, str], payload: Mapping[str, Any],
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    body = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", **headers}
    raw = _request_bytes("POST", url, h, body, timeout)
    return _parse(raw)


def get_json(
    url: str, headers: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    raw = _request_bytes("GET", url, headers, None, timeout)
    return _parse(raw)


def _parse(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise AiError("Provider returned malformed JSON") from exc
    return data if isinstance(data, dict) else {"data": data}


def _stream_lines(
    url: str, headers: Mapping[str, str], payload: Mapping[str, Any], timeout: float,
) -> Iterator[bytes]:
    body = json.dumps(payload).encode("utf-8")
    h = _with_defaults({"Content-Type": "application/json", **headers})
    req = urllib.request.Request(url, data=body, method="POST", headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        raise map_status(exc.code, str(getattr(exc, "reason", "")), detail) from exc
    except TimeoutError as exc:
        raise AiTimeoutError(f"Request to {url} timed out") from exc
    except urllib.error.URLError as exc:
        raise ProviderUnavailable(f"Cannot reach {url}: {exc.reason}") from exc
    with resp:
        for line in resp:
            yield line


async def aiter_stream_lines(
    url: str, headers: Mapping[str, str], payload: Mapping[str, Any],
    timeout: float = DEFAULT_TIMEOUT,
) -> AsyncIterator[bytes]:
    """Yield raw response lines from a streaming POST, reading the socket on a
    worker thread so the event loop stays free."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _pump() -> None:
        try:
            for line in _stream_lines(url, headers, payload, timeout):
                loop.call_soon_threadsafe(queue.put_nowait, ("line", line))
            loop.call_soon_threadsafe(queue.put_nowait, ("done", None))
        except Exception as exc:  # noqa: BLE001 - marshalled to the consumer
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

    task = asyncio.create_task(asyncio.to_thread(_pump))
    try:
        while True:
            kind, value = await queue.get()
            if kind == "line":
                yield value
            elif kind == "error":
                raise value if isinstance(value, AiError) else AiError(str(value))
            else:
                break
    finally:
        task.cancel()
