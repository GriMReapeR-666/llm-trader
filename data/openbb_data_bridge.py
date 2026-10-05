"""OpenBB Data Bridge — connects OpenBB SDK to MacroGraph ingestion.

Import-safe: works whether ``openbb`` is installed or not.
When ``openbb`` is unavailable the bridge reports *degraded* status
but never raises.  Every fetch flows through the dedup gate in
``openbb_provider_registry`` so that existing FRED / YFinance / SEC
data is never double-ingested.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from data.openbb_provider_registry import (
    PROVIDERS,
    ProviderConfig,
    FetcherEndpoint,
    active_providers,
    unique_endpoint_count,
)

# ── import-safe OpenBB SDK ──────────────────────────────────────────────────
# -- import-safe OpenBB SDK -----------------------------------------------------
# `from openbb import obb` builds OpenBB's extension package index at IMPORT
# time -- 31 extensions, printed as "Extensions to add: ..." then "Building..." --
# before a single call is made. Under pytest that cost is pure waste and, on a
# memory-constrained host, fatal: with `-n auto` the xdist workers exhaust RAM and
# the run dies as MemoryError, [WinError 1455] (paging file too small), or a native
# access violation inside torch, depending on which allocation loses the race.
#
# Honour the same pins this repo already uses for heavyweight dependencies
# (LLM_TRADER_YF_OFFLINE for yfinance, FAST_TEST for the 421M Laya checkpoint).
# Production is unaffected: neither variable is set there, so the real SDK still
# loads exactly as before and OPENBB_AVAILABLE reflects reality.
_OPENBB_PINNED_OFFLINE = bool(
    os.environ.get("LLM_TRADER_OPENBB_OFFLINE", "")
    or os.environ.get("FAST_TEST", "")
)
if _OPENBB_PINNED_OFFLINE:
    obb = None  # type: ignore[assignment]
    OPENBB_AVAILABLE = False
    logging.getLogger("OpenBBDataBridge").debug(
        "OpenBB import suppressed by offline/test pin; bridge runs degraded."
    )
else:
    try:
        from openbb import obb  # type: ignore[import-untyped]
        OPENBB_AVAILABLE = True
    except Exception:
        obb = None  # type: ignore[assignment]
        OPENBB_AVAILABLE = False

# ── import-safe MacroGraph ──────────────────────────────────────────────────
try:
    from intelligence.macro_graph import MacroGraph
except ImportError:
    MacroGraph = None  # type: ignore[misc,assignment]

log = logging.getLogger("OpenBBDataBridge")


# ── helpers ─────────────────────────────────────────────────────────────────

def _resolve_obb_endpoint(router: str, endpoint: str):
    """Walk obb.<router>.<endpoint> dotted path and return the callable, or None."""
    if obb is None:
        return None
    obj = obb
    for part in router.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    for part in endpoint.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


def _payload_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class ProviderHealth:
    """Per-provider circuit state (in-memory, non-persistent)."""

    def __init__(self, max_failures: int = 5, cooldown_seconds: int = 300):
        self._failures: Dict[str, List[float]] = {}
        self._max = max_failures
        self._cooldown = cooldown_seconds

    def record_failure(self, provider: str) -> None:
        self._failures.setdefault(provider, []).append(time.time())

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)

    def is_open(self, provider: str) -> bool:
        """True if the provider is healthy (circuit closed). False if tripped."""
        recent = self._failures.get(provider, [])
        cutoff = time.time() - self._cooldown
        recent = [t for t in recent if t > cutoff]
        self._failures[provider] = recent
        return len(recent) < self._max

    def status(self, provider: str) -> Dict[str, Any]:
        recent = self._failures.get(provider, [])
        cutoff = time.time() - self._cooldown
        recent = [t for t in recent if t > cutoff]
        return {
            "provider": provider,
            "circuit": "CLOSED" if len(recent) < self._max else "OPEN",
            "recent_failures": len(recent),
            "max_failures": self._max,
        }


class RateLimitGate:
    """Simple per-provider token-bucket rate limiter."""

    def __init__(self):
        self._last_call: Dict[str, float] = {}

    def wait_if_needed(self, provider: str, rpm: int) -> None:
        if rpm <= 0:
            return
        min_interval = 60.0 / rpm
        last = self._last_call.get(provider, 0.0)
        elapsed = time.time() - last
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_call[provider] = time.time()


# ── main bridge ─────────────────────────────────────────────────────────────

class OpenBBDataBridge:
    """Unified bridge from OpenBB SDK → MacroGraph ingestion.

    Parameters
    ----------
    store : TradingStore-compatible object with get_state/set_state, or None
        When provided the bridge auto-initialises a MacroGraph for ingestion.
    """

    def __init__(self, store=None, default_time_budget_s: Optional[float] = None):
        self._store = store
        self._graph: Optional[Any] = None
        if store is not None and MacroGraph is not None:
            self._graph = MacroGraph(store)
        self._health = ProviderHealth()
        self._rate = RateLimitGate()
        self._fetch_log: List[Dict[str, Any]] = []
        if default_time_budget_s is None:
            try:
                default_time_budget_s = float(
                    os.environ.get("OPENBB_FETCH_TIME_BUDGET_S", "45"))
            except (TypeError, ValueError):
                default_time_budget_s = 45.0
        self._default_time_budget_s = max(0.0, float(default_time_budget_s))

    # ── public API ──────────────────────────────────────────────────────

    @staticmethod
    def is_available() -> bool:
        """True when the ``openbb`` SDK is importable."""
        return OPENBB_AVAILABLE

    @staticmethod
    def provider_count() -> int:
        return len(active_providers())

    @staticmethod
    def endpoint_count() -> int:
        return unique_endpoint_count()

    def provider_status(self) -> Dict[str, Dict[str, Any]]:
        """Health status for every registered provider."""
        result: Dict[str, Dict[str, Any]] = {}
        for name, cfg in PROVIDERS.items():
            h = self._health.status(name)
            h["active_fetchers"] = len(cfg.active_fetchers)
            h["total_fetchers"] = len(cfg.fetchers)
            h["data_type"] = cfg.data_type
            h["openbb_installed"] = OPENBB_AVAILABLE
            result[name] = h
        return result

    # ── single-endpoint fetch ───────────────────────────────────────────

    def fetch_and_ingest(
        self,
        provider: str,
        fetcher_name: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        time_budget_s: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Fetch one endpoint from OpenBB and ingest into MacroGraph.

        Returns (success, message).

        ``time_budget_s`` caps the *entire* call including serial HTTP
        retries: the SDK call runs in a watchdog thread and is abandoned on
        deadline (daemon thread — the process never blocks past the budget
        on a hung provider).
        """
        cfg = PROVIDERS.get(provider)
        if cfg is None:
            return False, f"provider '{provider}' not in registry"

        fetcher = next((f for f in cfg.fetchers if f.name == fetcher_name), None)
        if fetcher is None:
            return False, f"fetcher '{fetcher_name}' not found in provider '{provider}'"
        if fetcher.skip:
            return False, f"fetcher skipped (dedup): {fetcher.skip_reason}"

        if not OPENBB_AVAILABLE:
            return False, "openbb SDK not installed"

        if not self._health.is_open(provider):
            return False, f"provider '{provider}' circuit is OPEN (too many failures)"

        # Rate-limit
        self._rate.wait_if_needed(provider, cfg.rate_limit_rpm)

        # Resolve and call OpenBB
        fn = _resolve_obb_endpoint(fetcher.router, fetcher.endpoint)
        if fn is None:
            return False, f"could not resolve obb.{fetcher.router}.{fetcher.endpoint}"

        # Fast check: does OpenBB coverage support this provider for this endpoint?
        target_endpoint = f".{fetcher.router}.{fetcher.endpoint}"
        if obb is not None and hasattr(obb, "coverage") and hasattr(obb.coverage, "providers"):
            supported_cmds = obb.coverage.providers.get(provider)
            if supported_cmds is None:
                return False, f"provider '{provider}' is not supported by installed OpenBB extensions"
            if target_endpoint not in supported_cmds:
                return False, f"provider '{provider}' does not support endpoint '{target_endpoint}'"

        call_params = dict(getattr(fetcher, "default_params", {}) or {})
        if params:
            call_params.update(params)
        call_params["provider"] = provider

        # Fast check: are required arguments present?
        import inspect
        try:
            sig = inspect.signature(fn)
            missing = [
                name for name, p in sig.parameters.items()
                if name not in ("provider", "kwargs", "extra_params")
                and p.default is inspect.Parameter.empty
                and name not in call_params
            ]
            if missing:
                return False, f"fetcher '{fetcher_name}' requires parameters: {missing}"
        except Exception:
            pass

        try:
            from core.http_retry import call_with_retry

            budget = time_budget_s if time_budget_s is not None else self._default_time_budget_s
            if budget and budget > 0:
                outcome: Dict[str, Any] = {}

                def _run() -> None:
                    try:
                        outcome["result"] = call_with_retry(
                            lambda: fn(**call_params), max_retries=3, base_delay=0.8)
                    except Exception as exc:
                        outcome["error"] = exc

                worker = threading.Thread(target=_run, daemon=True,
                                          name=f"openbb-fetch-{provider}:{fetcher_name}")
                worker.start()
                worker.join(timeout=budget)
                if worker.is_alive():
                    self._health.record_failure(provider)
                    msg = f"OpenBB call timed out after {budget:.1f}s (time budget)"
                    log.warning(msg)
                    return False, msg
                if "error" in outcome:
                    raise outcome["error"]
                result = outcome.get("result")
            else:
                result = call_with_retry(lambda: fn(**call_params), max_retries=3, base_delay=0.8)
        except Exception as exc:
            self._health.record_failure(provider)
            msg = f"OpenBB call failed: {exc}"
            log.warning(msg)
            return False, msg

        self._health.record_success(provider)

        # Extract results
        try:
            if hasattr(result, "results"):
                data = result.results
                if hasattr(data, "__iter__") and not isinstance(data, (str, dict)):
                    payload_items = [
                        item.model_dump() if hasattr(item, "model_dump") else
                        item.dict() if hasattr(item, "dict") else
                        dict(item) if isinstance(item, dict) else str(item)
                        for item in data
                    ]
                else:
                    payload_items = (
                        data.model_dump() if hasattr(data, "model_dump") else
                        data.dict() if hasattr(data, "dict") else
                        data
                    )
            else:
                payload_items = str(result)
        except Exception as exc:
            return False, f"failed to extract results: {exc}"

        # Build payload for MacroGraph
        payload = {
            "provider": provider,
            "fetcher": fetcher_name,
            "data_type": cfg.data_type,
            "router": fetcher.router,
            "endpoint": fetcher.endpoint,
            "params": params or {},
            "record_count": len(payload_items) if isinstance(payload_items, list) else 1,
            "data": payload_items,
        }

        # Ingest into MacroGraph if available
        source_id = ""
        if self._graph is not None:
            try:
                source_id = self._graph.ingest_source(
                    title=f"OpenBB:{provider}:{fetcher_name}",
                    payload=payload,
                    source_url=cfg.website,
                    publisher=cfg.publisher_tag or f"openbb-{provider}",
                )
            except Exception as exc:
                log.warning("MacroGraph ingestion failed: %s", exc)
                source_id = f"ingest-error:{exc}"

        # Log (with a bounded preview of the first record so tools can surface
        # actual readings without holding the whole payload in memory)
        data_preview = None
        if isinstance(payload_items, list) and payload_items:
            first = payload_items[0]
            data_preview = first if isinstance(first, dict) else {"value": first}
        elif isinstance(payload_items, dict):
            data_preview = payload_items
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "fetcher": fetcher_name,
            "source_id": source_id,
            "records": payload.get("record_count", 0),
            "data_preview": data_preview,
        }
        self._fetch_log.append(entry)
        return True, source_id or f"fetched {payload.get('record_count', 0)} records (no graph)"

    # ── batch fetch by type ─────────────────────────────────────────────

    def fetch_by_type(
        self,
        data_type: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        time_budget_s: Optional[float] = None,
    ) -> List[Tuple[str, bool, str]]:
        """Fetch all active endpoints matching *data_type*.

        Returns [(provider:fetcher, success, message), ...].

        A batch shares one time budget: once exhausted, remaining endpoints
        are reported as skipped instead of continuing to burn serial retry
        time on providers that are down.
        """
        results: List[Tuple[str, bool, str]] = []
        budget = time_budget_s if time_budget_s is not None else self._default_time_budget_s
        deadline = (time.monotonic() + budget) if budget and budget > 0 else None
        for cfg in PROVIDERS.values():
            if cfg.data_type != data_type:
                continue
            for f in cfg.active_fetchers:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        results.append((f"{cfg.name}:{f.name}", False,
                                        "skipped: batch time budget exhausted"))
                        continue
                    step_budget = min(remaining, 5.0)
                else:
                    step_budget = 5.0
                ok, msg = self.fetch_and_ingest(
                    cfg.name, f.name, params=params, time_budget_s=step_budget,
                )
                results.append((f"{cfg.name}:{f.name}", ok, msg))
        return results

    def fetch_all_central_bank(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("central-bank-release", params=params, time_budget_s=time_budget_s)

    def fetch_all_official_stats(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("official-statistics", params=params, time_budget_s=time_budget_s)

    def fetch_all_exchange_data(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("exchange-data", params=params, time_budget_s=time_budget_s)

    def fetch_all_microstructure(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("market-microstructure", params=params, time_budget_s=time_budget_s)

    def fetch_all_regulatory(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("regulatory-filing", params=params, time_budget_s=time_budget_s)

    def fetch_all_screening(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("equity-screening", params=params, time_budget_s=time_budget_s)

    def fetch_all_macro(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("macro-indicators", params=params, time_budget_s=time_budget_s)

    def fetch_all_fixed_income(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("fixed-income", params=params, time_budget_s=time_budget_s)

    def fetch_all_calendars(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("calendar-events", params=params, time_budget_s=time_budget_s)

    def fetch_all_estimates(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("estimates-consensus", params=params, time_budget_s=time_budget_s)

    def fetch_all_quant_ratings(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("quant-ratings", params=params, time_budget_s=time_budget_s)

    def fetch_all_transcripts(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("transcripts", params=params, time_budget_s=time_budget_s)

    def fetch_all_factor_models(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("factor-model", params=params, time_budget_s=time_budget_s)

    def fetch_all_factor_screens(self, *, time_budget_s: Optional[float] = None, **params) -> List[Tuple[str, bool, str]]:
        return self.fetch_by_type("factor-screening", params=params, time_budget_s=time_budget_s)

    # ── full sweep ──────────────────────────────────────────────────────

    def fetch_all(self, *, params: Optional[Dict[str, Any]] = None) -> List[Tuple[str, bool, str]]:
        """Sweep every active endpoint across all providers."""
        results: List[Tuple[str, bool, str]] = []
        budget = self._default_time_budget_s
        deadline = (time.monotonic() + budget) if budget and budget > 0 else None
        for cfg in PROVIDERS.values():
            for f in cfg.active_fetchers:
                if deadline is not None and time.monotonic() >= deadline:
                    results.append((f"{cfg.name}:{f.name}", False,
                                    "skipped: sweep time budget exhausted"))
                    continue
                ok, msg = self.fetch_and_ingest(
                    cfg.name, f.name, params=params, time_budget_s=budget)
                results.append((f"{cfg.name}:{f.name}", ok, msg))
        return results

    # ── diagnostics ─────────────────────────────────────────────────────

    def recent_fetches(self, n: int = 20) -> List[Dict[str, Any]]:
        """Return the last *n* fetch log entries."""
        return self._fetch_log[-n:]

    def summary(self) -> Dict[str, Any]:
        """Overall bridge summary."""
        return {
            "openbb_installed": OPENBB_AVAILABLE,
            "registered_providers": len(PROVIDERS),
            "active_providers": len(active_providers()),
            "unique_endpoints": unique_endpoint_count(),
            "total_fetches": len(self._fetch_log),
            "provider_health": {
                name: self._health.status(name) for name in PROVIDERS
            },
        }
