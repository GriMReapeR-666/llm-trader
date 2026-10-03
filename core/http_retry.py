"""Shared exponential-backoff HTTP retry helper (GAP-14)."""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Optional

import requests

log = logging.getLogger("HttpRetry")


def request_with_retry(
    session: Optional[requests.Session],
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 0.75,
    timeout: float = 20.0,
    retry_statuses: tuple = (429, 500, 502, 503, 504),
    **kwargs: Any,
) -> requests.Response:
    """Perform an HTTP request with exponential backoff on transient failures."""
    client = session or requests
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, int(max_retries))):
        try:
            response = client.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in retry_statuses and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.35)
                log.warning("HTTP %s for %s — retry in %.1fs (%s/%s)",
                            response.status_code, url, delay, attempt + 1, max_retries)
                time.sleep(delay)
                continue
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= max_retries - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.35)
            log.warning("Request error for %s (%s) — retry in %.1fs", url, exc, delay)
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"request_with_retry exhausted for {url}")


def call_with_retry(fn: Callable[[], Any], *, max_retries: int = 3, base_delay: float = 0.75) -> Any:
    """Retry an arbitrary callable on Exception (for SDK wrappers)."""
    last_exc: Optional[Exception] = None
    for attempt in range(max(1, int(max_retries))):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if (
                isinstance(exc, (TypeError, ValueError))
                or "ValidationError" in type(exc).__name__
                or "OpenBBError" in type(exc).__name__
                or "OpenBBError" in [b.__name__ for b in type(exc).__mro__]
            ):
                raise exc
            if attempt >= max_retries - 1:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.35)
            log.warning("Callable retry after error (%s) in %.1fs", exc, delay)
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
