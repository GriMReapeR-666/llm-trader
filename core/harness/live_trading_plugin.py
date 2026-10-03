"""Live Trading Loop as Harness Plugin — surgical pluginification.

Makes the live autonomous loop (MarketData + LLMOrchestrator + Risk/Trade)
available via TradingContext, so `mt5_trader llm-trade --harness` can run
everything as plugins with CapabilitySandbox enforcement.

A single LiveTradingPlugin provides the loop factory, the sandbox, and the
per-cycle BOCPD/fractional-diff/Kalman/meta advisories (previously split
across LiveTradingLoopPlugin + LiveAdvisoryHubPlugin, which even provided
the same advisory snapshot under two keys).

Surgical: does NOT replace direct path — provides dual path. No behavior change
when --harness not used. When --harness used, every component is `ctx.provide`.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional
from core.harness.kernel import HarnessPlugin, TradingContext
from core.harness.sandbox import PermissionLevel

log = logging.getLogger(__name__)

class LiveTradingPlugin(HarnessPlugin):
    """Live loop + per-cycle advisories behind one plugin.

    Provides: live_advisory_hub_fn, llm_orchestrator_factory, sandbox,
    build_live_advisory_snapshot, bocpd_advisory, fractional_diff_advisory,
    kalman_advisory, meta_label_advisory.
    """
    name = "live_trading"
    version = "1.0.0"
    dependencies = []

    def __init__(self, paper_mode: bool = True, db_path: str = "trading_state.db", api_key: str = ""):
        self.paper_mode = paper_mode
        self.db_path = db_path
        self.api_key = api_key
        # Late-bound orchestrator kwargs. The entrypoint (mt5_trader) supplies
        # the runtime arguments (client, perf_monitor, api_key, model_name, ...)
        # via configure_orchestrator() or by calling the factory with overrides,
        # so the plugin-provided factory produces the exact same LLMOrchestrator
        # as the direct construction path.
        self.orchestrator_kwargs: Dict[str, Any] = {}

    def configure_orchestrator(self, **kwargs: Any) -> None:
        """Bind/override constructor kwargs used by the orchestrator factory."""
        self.orchestrator_kwargs.update(kwargs)

    def apply(self, ctx: TradingContext) -> None:
        # --- MarketData already provided by DataHubPlugin, reuse ---
        market_data = ctx.get("market_data")
        risk_mgr = ctx.get("risk_manager")
        store = ctx.get("trading_store")
        broker = ctx.get("broker")

        # --- LiveAdvisoryHub as plugin-provided pure functions ---
        from core.live_advisory_hub import (
            build_live_advisory_snapshot,
            bocpd_advisory,
            fractional_diff_advisory,
            kalman_advisory,
            meta_label_advisory,
        )
        ctx.provide("live_advisory_hub_fn", build_live_advisory_snapshot)
        ctx.provide("build_live_advisory_snapshot", build_live_advisory_snapshot)
        ctx.provide("bocpd_advisory", bocpd_advisory)
        ctx.provide("fractional_diff_advisory", fractional_diff_advisory)
        ctx.provide("kalman_advisory", kalman_advisory)
        ctx.provide("meta_label_advisory", meta_label_advisory)

        # --- LLMOrchestrator as plugin (deferred construction) ---
        def make_orchestrator(**overrides: Any):
            from intelligence.llm_orchestrator import LLMOrchestrator
            kwargs = dict(self.orchestrator_kwargs)
            # Explicitly-passed arguments win even when their value is None:
            # the boot path passes client=None in fail-closed no-broker mode
            # (weekend, terminal down) and the orchestrator tolerates it.
            # Only arguments the caller did NOT provide are back-filled/
            # enforced below.
            explicit = set(overrides.keys())
            if overrides:
                kwargs.update(overrides)
            if not kwargs.get("risk_manager"):
                kwargs["risk_manager"] = risk_mgr
            missing = [name for name in ("client", "risk_manager", "perf_monitor", "api_key")
                       if kwargs.get(name) is None and name not in explicit]
            if missing:
                raise TypeError(
                    "LLMOrchestrator factory missing required arguments: "
                    + ", ".join(missing)
                    + ". Pass them via make_orchestrator(**orch_kwargs) or "
                    + "LiveTradingPlugin.configure_orchestrator(**orch_kwargs)."
                )
            return LLMOrchestrator(**kwargs)
        ctx.provide("llm_orchestrator_factory", make_orchestrator)

        # --- Execution guard: wrap order_execution via sandbox ---
        from core.harness.sandbox import CapabilitySandbox
        sandbox = ctx.get("sandbox")
        if sandbox is None:
            sandbox = CapabilitySandbox(default_level=PermissionLevel.SIMULATION if self.paper_mode else PermissionLevel.ORDER_WRITE)
            ctx.provide("sandbox", sandbox)

        log.info("LiveTradingPlugin mounted (paper=%s) — loop factory, sandbox, live advisories via TradingContext", self.paper_mode)
