"""Quota-pause: a 429 carrying a reset timestamp parks the provider until
the reset instead of burning sleep-retry cycles on every decision.

Covers: reset parsing (Retry-After / X-RateLimit-Reset epoch-ms|s / ISO
fallback), provider-level pause short-circuit, clamping, recovery, and
router-level skipping of paused providers (including the paused-primary,
failover-off case going straight to deterministic HOLD).
"""
import json
import re
import time
import types
from datetime import datetime, timezone

from data.llm_router import (
    BaseLLMProvider,
    LLMRouter,
    OpenRouterProvider,
    parse_rate_limit_reset,
)


class _FakeRateLimitError(Exception):
    """429-shaped exception with an SDK-style attached response."""

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


def _openrouter_429(reset_epoch_ms):
    body = json.dumps({
        "error": {
            "message": "Rate limit exceeded: free-models-per-day. "
                       "Add 10 credits to unlock 1000 free model requests/day.",
            "code": 429,
        }
    })
    return _FakeRateLimitError(
        f"Error code: 429 - {body}",
        response=types.SimpleNamespace(headers={
            "X-RateLimit-Reset": str(int(reset_epoch_ms)),
            "Content-Type": "application/json",
        }),
    )


def _retry_after_429(seconds):
    return _FakeRateLimitError(
        "Error code: 429 - Rate limit exceeded",
        response=types.SimpleNamespace(headers={"Retry-After": str(seconds)}),
    )


class _RateLimitedProvider(BaseLLMProvider):
    """Always raises the configured exception via the real retry loop."""

    provider_name = "rate_limited_mock"

    def __init__(self, exc_factory, **kwargs):
        super().__init__(api_keys=["k"], **kwargs)
        self._exc_factory = exc_factory
        self.calls = 0

    def generate(self, messages, model, **kwargs):
        def request_fn():
            self.calls += 1
            raise self._exc_factory()
        return self._retry_with_backoff(request_fn)


class _RecoveringProvider(BaseLLMProvider):
    """Raises the 429 once, then succeeds — models quota-reset recovery."""

    provider_name = "recovering_mock"

    def __init__(self, exc, **kwargs):
        super().__init__(api_keys=["k"], **kwargs)
        self._exc = exc
        self.calls = 0

    def generate(self, messages, model, **kwargs):
        def request_fn():
            self.calls += 1
            if self.calls == 1:
                raise self._exc
            return "RECOVERED"
        return self._retry_with_backoff(request_fn)


class _PlainProvider:
    """Minimal router-level provider without quota machinery."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def generate(self, messages, model, **kwargs):
        self.calls += 1
        return self.result


# ---------------------------------------------------------------------------
# parse_rate_limit_reset
# ---------------------------------------------------------------------------

def test_parse_retry_after_header_returns_absolute_ts():
    before = time.time()
    parsed = parse_rate_limit_reset(_retry_after_429(120))
    assert parsed is not None
    assert before + 119 <= parsed <= time.time() + 121


def test_parse_xratelimit_reset_epoch_ms():
    expected = time.time() + 3600
    parsed = parse_rate_limit_reset(_openrouter_429(expected * 1000))
    assert abs(parsed - expected) < 5


def test_parse_xratelimit_reset_epoch_seconds():
    expected = time.time() + 600
    exc = _FakeRateLimitError(
        "Error code: 429 - Rate limit exceeded",
        response=types.SimpleNamespace(headers={"X-RateLimit-Reset": str(int(expected))}),
    )
    assert abs(parse_rate_limit_reset(exc) - expected) < 5


def test_parse_iso_timestamp_in_body():
    exc = _FakeRateLimitError(
        "429 Rate limit exceeded. Resets at 2030-01-01T00:00:00Z.")
    expected = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    assert abs(parse_rate_limit_reset(exc) - expected) < 1


def test_parse_returns_none_without_reset_signals():
    exc = _FakeRateLimitError(
        "Error code: 429 - Rate limit exceeded: openrouter_free_tier_daily")
    assert parse_rate_limit_reset(exc) is None


# ---------------------------------------------------------------------------
# Provider-level pause behavior
# ---------------------------------------------------------------------------

def test_quota_pause_short_circuits_retries():
    """One 429 with a far-future reset = exactly ONE upstream call, no sleeps."""
    provider = _RateLimitedProvider(
        lambda: _openrouter_429((time.time() + 6 * 3600) * 1000),
        max_retries=3, base_delay=0.0)
    result = provider.generate([], "model-x")
    assert provider.calls == 1
    assert provider._is_quota_paused()
    assert result.startswith("Error: rate_limited_mock quota exhausted (429)")
    assert "paused until quota reset" in result


def test_429_without_reset_still_uses_backoff_retries():
    provider = _RateLimitedProvider(
        lambda: _FakeRateLimitError("Error code: 429 - Rate limit exceeded: daily"),
        max_retries=2, base_delay=0.0)
    result = provider.generate([], "model-x")
    assert provider.calls == 2          # retried, then gave up via backoff
    assert not provider._is_quota_paused()
    assert result.startswith("Error: rate_limited_mock failed:")


def test_small_reset_window_does_not_pause():
    provider = _RateLimitedProvider(
        lambda: _openrouter_429((time.time() + 30) * 1000),
        max_retries=2, base_delay=0.0,
        quota_pause_min_seconds=60)
    provider.generate([], "model-x")
    assert provider.calls == 2          # minute-scale window -> backoff path
    assert not provider._is_quota_paused()


def test_pause_duration_is_clamped_to_max():
    provider = _RateLimitedProvider(
        lambda: _openrouter_429((time.time() + 10 * 86400) * 1000),
        max_retries=1, base_delay=0.0,
        quota_pause_max_seconds=3600)
    result = provider.generate([], "model-x")
    assert provider._is_quota_paused()
    remaining = provider.quota_paused_until - time.time()
    assert 3500 <= remaining <= 3600 + 30   # cap (+ margin allowance)
    match = re.search(r"~(\d+)s remaining", result)
    assert match, result
    assert 3500 <= int(match.group(1)) <= 3600


def test_pause_expires_and_success_clears_state():
    provider = _RecoveringProvider(
        _openrouter_429((time.time() + 2) * 1000),
        max_retries=1, base_delay=0.0,
        quota_pause_margin_seconds=0.0, quota_pause_min_seconds=0.0)
    first = provider.generate([], "model-x")
    assert provider.calls == 1
    assert provider._is_quota_paused()

    # Simulate the wall clock reaching the reset.
    provider.quota_paused_until = time.time() - 0.1
    assert not provider._is_quota_paused()

    second = provider.generate([], "model-x")
    assert second == "RECOVERED"
    assert provider.calls == 2
    assert provider.quota_paused_until == 0.0   # _record_success clears pause


def test_pause_message_carries_remaining_seconds():
    provider = _RateLimitedProvider(
        lambda: _openrouter_429((time.time() + 900) * 1000),
        max_retries=1, base_delay=0.0)
    provider.generate([], "model-x")
    result = provider.generate([], "model-x")   # now hits the pause gate
    assert "Provider rate_limited_mock paused until quota reset" in result
    assert "~" in result and "s remaining" in result


# ---------------------------------------------------------------------------
# Router-level skipping of paused providers
# ---------------------------------------------------------------------------

def _paused_primary():
    primary = _RateLimitedProvider(
        lambda: _openrouter_429((time.time() + 6 * 3600) * 1000),
        max_retries=3, base_delay=0.0)
    primary.generate([], "m")   # trip the pause once
    return primary


def _router_with(primary, fallback=None, allow_failover=True):
    router = LLMRouter.__new__(LLMRouter)
    providers = {"primary": primary}
    if fallback is not None:
        providers["fallback"] = fallback
    router.providers = providers
    router.agents = {"trader": {
        "provider": "primary", "model": "m",
        "fallback_providers": ["fallback"] if fallback else [],
        "allow_failover": allow_failover}}
    return router


def test_router_skips_paused_provider_without_calling_it():
    primary = _paused_primary()
    fallback = _PlainProvider("fallback result")
    router = _router_with(primary, fallback)
    result = router.generate("trader", [{"role": "user", "content": "x"}])
    assert result == "fallback result"
    assert primary.calls == 1           # pause-trip call only; router skipped it


def test_paused_primary_without_failover_returns_immediate_hold():
    primary = _paused_primary()
    router = _router_with(primary, fallback=None, allow_failover=False)
    payload = json.loads(
        router.generate("trader", [{"role": "user", "content": "x"}]))
    assert payload["action"] == "HOLD"
    assert payload["deterministic_fallback"] is True
    assert primary.calls == 1           # no network attempt while paused
    assert any("paused until quota reset" in err and "skipped" in err
               for err in payload["provider_errors"])


def test_get_status_reports_paused_quota():
    primary = _paused_primary()
    router = _router_with(primary, fallback=None, allow_failover=False)
    status = router.get_status()["providers"]["primary"]
    assert status["status"] == "PAUSED_QUOTA"
    assert status["quota_paused_until"] > time.time()


def test_openrouter_429_error_string_is_detected_as_rate_limit():
    """The real OpenRouter daily-cap body must classify as a 429 rate limit."""
    provider = _RateLimitedProvider(
        lambda: _openrouter_429((time.time() + 6 * 3600) * 1000),
        max_retries=3, base_delay=0.0)
    result = provider.generate([], "model-x")
    assert "429" in result
    assert provider.calls == 1
