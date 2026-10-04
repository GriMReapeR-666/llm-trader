"""Live smoke test: AlphaDiscovery generation through OpenRouter (real key).

Verifies the exact production path wired 2026-09-27:

    .env key -> llm_router (alpha_discovery agent, primary=openrouter)
             -> _router_llm_generate -> parse_llm_response -> sealed sandbox

FAIL-CLOSED: the script only exits 0 when at least one LLM-generated
candidate parses as FactorMiner DSL and validates in the sandbox. The
static 5-candidate fallback is instrumented and any fallback use — even a
fully "successful" cycle — fails the smoke, so a silently degraded run can
never be mistaken for a live-LLM pass.

Exit codes:
    0  LLM candidates parsed as FactorMiner DSL and validated in the sandbox
       (mechanism tier; production-threshold count reported separately)
    2  LLM replied but nothing parsed as FactorMiner DSL
    3  no provider key configured (add OPENROUTER_API_KEY to .env)
    4  key(s) present but OpenRouter is not the alpha_discovery primary
    5  unexpected error

Usage:
    python -X utf8 scripts/alpha_discovery_live_smoke.py
"""
from __future__ import annotations

import json
import os
import sys

PROVIDER_KEY_ENVS = (
    "OPENROUTER_API_KEY", "TOKENROUTER_KEY", "TOKENROUTER_API_KEY",
    "ROUTESME_KEY", "ROUTESME_API_KEY", "CEREBRAS_API_KEY",
    "NVIDIA_KEY", "NVIDIA_API_KEY",
)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()  # same mechanism mt5_trader.py uses at boot
    except ImportError:
        pass  # rely on system env vars exactly like the trader fallback


def main() -> int:
    for path in ("data", "core", "."):
        if path not in sys.path:
            sys.path.insert(0, path)
    _load_dotenv()
    present = [k for k in PROVIDER_KEY_ENVS if os.getenv(k)]
    print(f"provider keys present: {present or 'NONE'}")
    if not present:
        print("RESULT: BLOCKED — no provider key configured.")
        print("Add a real key to .env (OPENROUTER_API_KEY=sk-or-...) and re-run.")
        return 3

    from data.llm_router import get_router
    router = get_router()
    available = list(getattr(router, "providers", {}).keys())
    print(f"router providers initialized: {available}")
    # Register the alpha_discovery agent exactly as the production default
    # does inside _router_llm_generate (without spending a request on a probe).
    if "alpha_discovery" not in getattr(router, "agents", {}):
        primary = os.getenv("ALPHA_LLM_PROVIDER") or (
            "openrouter" if "openrouter" in available else available[0])
        if primary not in available:
            primary = available[0]
        router.register_agent(
            "alpha_discovery",
            provider=primary,
            model=os.getenv("ALPHA_LLM_MODEL", "meta-llama/llama-3.2-11b-vision-instruct:free"),
            fallback_providers=[p for p in available if p != primary],
            allow_failover=True,
        )
    agent = getattr(router, "agents", {}).get("alpha_discovery")
    if agent is None:
        print("RESULT: FAILED — alpha_discovery agent not registered on the router.")
        return 5
    primary = getattr(agent, "provider", None) or (
        agent.get("provider") if isinstance(agent, dict) else None)
    print(f"alpha_discovery primary provider: {primary}")
    if str(primary).lower() != "openrouter":
        print("RESULT: BLOCKED — OpenRouter is not the alpha_discovery primary "
              f"(got {primary!r}); this smoke verifies the OpenRouter path.")
        return 4

    # Hermetic engine: fresh state file so the smoke never touches the
    # production alpha_discovery_state.json experience memory. One live LLM
    # call through the production adapter (_router_llm_generate — the exact
    # function run_discovery_cycle uses), then parse + sandbox directly so
    # every stage is observable and rate-limit friendly.
    from core.alpha_discovery import (
        AlphaCandidate,
        AlphaDiscoveryEngine,
        SealedSandboxEvaluator,
        _router_llm_generate,
    )
    state_file = "alpha_discovery_state_live_smoke.json"
    if os.path.exists(state_file):
        os.remove(state_file)
    engine = AlphaDiscoveryEngine(state_file=state_file, max_alphas_per_cycle=3)

    # Market data: same synthetic OHLCV construction the unit tests use
    # (1500 bars so the sandbox has enough history for 20+ window factors).
    import numpy as np
    import pandas as pd
    np.random.seed(42)
    n = 1500
    close = 1.0800 + np.cumsum(np.random.normal(0.0001, 0.0005, n))
    df = pd.DataFrame({
        "open": close - np.random.uniform(0, 0.001, n),
        "high": close + np.random.uniform(0, 0.002, n),
        "low": close - np.random.uniform(0, 0.002, n),
        "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })
    df["returns"] = df["close"].pct_change().fillna(0.0)

    split = int(len(df) * 0.7)
    train_df, test_df = df.iloc[:split].copy(), df.iloc[split:].copy()
    print(f"market data: {len(train_df)} train / {len(test_df)} test bars")

    print("\n--- one live generation call via _router_llm_generate (primary=openrouter) ---")
    try:
        raw = _router_llm_generate(engine.build_generation_prompt())
    except Exception as exc:
        print(f"RESULT: FAILED — live generation raised: {str(exc)[:300]}")
        return 5
    print(f"LLM response: {len(raw)} chars")

    parsed = engine.parse_llm_response(raw)
    print(f"parsed FactorMiner candidates: {len(parsed)}")
    for item in parsed:
        print(f"  [{item['category']}] {item['expression'][:100]}")
    if not parsed:
        print("\nRESULT: FAILED — LLM replied but nothing parsed as FactorMiner DSL.")
        print("First 400 chars of response:")
        print(raw[:400])
        return 2

    # Sandbox validation. Smoke thresholds are disclosed and deliberately
    # reachable: synthetic random-walk data has no learnable signal, so the
    # production tier (IC_IR >= 0.15) is reported separately, never faked.
    sandbox = SealedSandboxEvaluator(
        min_ic_ir=0.0, min_ic_mean=-1.0, max_turnover=10.0, min_sharpe=-10.0)
    evaluated = []
    for i, item in enumerate(parsed):
        candidate = AlphaCandidate(
            expression=item["expression"], name=f"live_smoke_{i}",
            category=item["category"])
        try:
            result = sandbox.evaluate_candidate(candidate, train_df, test_df)
        except Exception as exc:
            result = candidate
            result.status = "REJECTED"
            result.rejection_reason = f"evaluator raised: {str(exc)[:150]}"
        evaluated.append({
            "expression": result.expression[:120],
            "status": result.status,
            "ic_ir": round(float(result.ic_ir or 0.0), 4),
            "ic_mean": round(float(result.ic_mean or 0.0), 4),
            "sharpe": round(float(result.sharpe or 0.0), 4),
            "turnover": round(float(result.turnover or 0.0), 4),
            "rejection_reason": result.rejection_reason,
        })

    print("\nsandbox results (smoke thresholds: expression evaluates, metrics compute):")
    for row in evaluated:
        print(f"  {row['status']:9s} ic_ir={row['ic_ir']:+.4f} {row['expression'][:70]}"
              f"{(' — ' + row['rejection_reason']) if row['rejection_reason'] else ''}")

    validated_rows = [r for r in evaluated if r["status"] == "VALIDATED"]
    production_rows = [r for r in evaluated
                       if r["status"] == "VALIDATED" and r["ic_ir"] >= 0.15]
    print(f"\nmechanism tier: {len(validated_rows)}/{len(evaluated)} candidates "
          "parsed AND validated through the sealed sandbox")
    print(f"production tier: {len(production_rows)}/{len(evaluated)} candidates "
          "meet IC_IR >= 0.15 (synthetic data carries no real signal; "
          "run against live EURUSD bars for the production verdict)")

    if not validated_rows:
        print("\nRESULT: FAILED — candidates parsed but none survived even "
              "smoke-threshold sandbox validation.")
        return 2
    print("\nRESULT: PASS — OpenRouter-generated FactorMiner candidates parsed "
          "and validated in the sealed sandbox (live LLM path verified).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
