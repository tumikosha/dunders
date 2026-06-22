"""Guardrails: token meter + budget, response cache, PII redaction, no-AI zones."""

from __future__ import annotations

import pytest

from dunders.ai.guardrails import (
    NOAI_MARKER,
    Redactor,
    ResponseCache,
    TokenMeter,
    is_ai_allowed,
)
from dunders.ai.provider import BudgetExceededError
from dunders.ai.types import ChatRequest, ChatResponse, Usage, user


# --- TokenMeter ------------------------------------------------------------


def test_meter_accumulates_and_prices():
    meter = TokenMeter()
    meter.record("claude-opus-4-8", Usage(input_tokens=1_000_000, output_tokens=0))
    # 1M input tokens * $5/1M = $5
    assert meter.total().cost_usd == pytest.approx(5.0)
    assert meter.total().input_tokens == 1_000_000


def test_budget_raises():
    meter = TokenMeter(budget_usd=1.0)
    meter.record("claude-opus-4-8", Usage(input_tokens=1_000_000))  # $5 spent
    with pytest.raises(BudgetExceededError):
        meter.check_budget()


def test_budget_ok_under_limit():
    meter = TokenMeter(budget_usd=100.0)
    meter.record("gpt-oss-120b", Usage(input_tokens=1000))  # free model
    meter.check_budget()  # no raise


# --- ResponseCache ---------------------------------------------------------


def _req():
    return ChatRequest(messages=[user("hi")], model="m")


def test_cache_hit_and_miss():
    cache = ResponseCache()
    req = _req()
    key = cache.key("fake", req)
    assert cache.get(key) is None
    cache.set(key, ChatResponse(text="cached"))
    assert cache.get(key).text == "cached"


def test_cache_key_depends_on_messages():
    cache = ResponseCache()
    k1 = cache.key("fake", ChatRequest(messages=[user("a")]))
    k2 = cache.key("fake", ChatRequest(messages=[user("b")]))
    assert k1 != k2


def test_cache_ttl_expiry():
    cache = ResponseCache(ttl=10)
    key = cache.key("fake", _req())
    cache.set(key, ChatResponse(text="x"), now=100.0)
    assert cache.get(key, now=105.0) is not None
    assert cache.get(key, now=200.0) is None


def test_cache_lru_eviction():
    cache = ResponseCache(maxsize=2)
    for i in range(3):
        cache.set(f"k{i}", ChatResponse(text=str(i)))
    assert cache.get("k0") is None  # evicted
    assert cache.get("k2") is not None


# --- Redactor --------------------------------------------------------------


def test_redact_email_and_keys():
    r = Redactor()
    out = r.redact("mail me at a.b@example.com with sk-ABCDEFGHIJKLMNOP12345")
    assert "example.com" not in out
    assert "sk-ABCD" not in out
    assert "[REDACTED_EMAIL]" in out
    assert "[REDACTED_KEY]" in out


def test_redact_leaves_plain_text():
    r = Redactor()
    assert r.redact("just some words") == "just some words"


def test_redact_request_copies_messages():
    r = Redactor()
    req = ChatRequest(messages=[user("ping x@y.com")], system="hi z@w.com")
    out = r.redact_request(req)
    assert "x@y.com" not in out.messages[0].text()
    assert "z@w.com" not in (out.system or "")
    # original untouched
    assert "x@y.com" in req.messages[0].text()


# --- no-AI zones -----------------------------------------------------------


def test_local_always_allowed(tmp_path):
    (tmp_path / NOAI_MARKER).touch()
    assert is_ai_allowed(tmp_path / "f.txt", cloud=False) is True


def test_marker_blocks_cloud(tmp_path):
    (tmp_path / NOAI_MARKER).touch()
    assert is_ai_allowed(tmp_path / "f.txt", cloud=True) is False


def test_marker_blocks_in_subdir(tmp_path):
    (tmp_path / NOAI_MARKER).touch()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert is_ai_allowed(sub / "f.txt", cloud=True) is False


def test_glob_blocks_cloud(tmp_path):
    assert is_ai_allowed("/secret/data.txt", cloud=True, globs=["/secret/*"]) is False


def test_clean_path_allowed(tmp_path):
    assert is_ai_allowed(tmp_path / "f.txt", cloud=True) is True


def test_none_path_allowed():
    assert is_ai_allowed(None, cloud=True) is True
