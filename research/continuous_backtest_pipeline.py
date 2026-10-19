"""Continuous Backtesting Pipeline

Automated scheduled backtesting that runs periodically and feeds results
into promotion gates. This is the core of the autonomous validation system.

Runs:
- Full pipeline backtest (indicator -> gates -> execution)
- DSR gate validation
- Walk-forward analysis
- Live-vs-backtest comparison
- Degradation detection

Usage:
    # Run once
    python continuous_backtest_pipeline.py --once

    # Run continuously (every 4 hours)
    python continuous_backtest_pipeline.py --interval 14400

    # Run for specific symbol
    python continuous_backtest_pipeline.py --symbol EURUSD --timeframe H1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

log = logging.getLogger("ContinuousBacktest")

SECONDS_PER_YEAR = 365.0 * 24 * 3600


def _broker_confirmed_tickets(db_path: str) -> set:
    """Tickets with independent broker evidence in this journal.

    Empty means no admissible evidence: a trades row asserting
    source="broker" is self-declared and grades nothing. Every performance
    stage below (walk-forward, CPCV, live comparison) must filter through
    this or a synthetic fixture becomes "validated" performance.
    """
    try:
        from research.research_validation import broker_reconciled_tickets
    except ImportError:
        from research_validation import broker_reconciled_tickets
    return broker_reconciled_tickets(db_path)


def _broker_reconciled_r_multiples(db_path: str) -> List[float]:
    """Realized R-multiples that carry independent broker evidence, oldest first.

    ``trades`` has **no** ``r_multiple`` column -- the R-multiple lives in
    ``trade_labels`` (and is derived from ``pnl / risk_amount`` when the label
    carries none).  The previous reader selected ``r_multiple`` from the
    ``trades`` table, which always raised ``no such column: r_multiple``, was
    swallowed by a bare ``except Exception: pass``, and therefore left
    walk-forward reporting "fewer than 20 broker-reconciled r-multiples"
    forever -- a broken query indistinguishable from a genuinely starved gate.

    Admission goes through ``labelled_dataset``, the same reader the promotion
    gate and the weekly-research CPCV use: a ticket counts only when
    ``broker_execution_reports``/``order_intents`` vouch for it independently,
    it carries a deterministic label, and it is not a quarantined synthetic
    fixture.  The >= 20 / >= 40 floors are untouched -- only the reader became
    correct, and an absent R-multiple is never synthesised to reach one.

    Raises on a broken journal.  A reader that cannot read must surface as a
    failed stage, never as an empty dataset that reads as "starved" forever.
    """
    try:
        from research.validation import labelled_dataset
    except ImportError:
        from research.research_validation import labelled_dataset
    dataset = labelled_dataset(db_path)
    multiples = [float(row["r_multiple"]) for row in dataset.get("rows", [])
                 if row.get("r_multiple") is not None]
    if not multiples:
        # "Starved" and "quarantined fixtures" are very different operator
        # problems and used to look identical from the outside.
        log.info("No broker-reconciled R-multiples in %s (excluded: %s)",
                 db_path, dataset.get("excluded", {}))
    return multiples


def _periods_per_year(timeframe: str) -> float:
    """Annualization constant matching the bar timeframe.

    The backtest defaults to the M15 constant, so passing an H1/H4/D1 series
    without this overstates Sharpe by sqrt(35040/8760) = 2x for H1.
    """
    from data.data_quality import timeframe_seconds

    seconds = timeframe_seconds(timeframe)
    if seconds <= 0:
        raise ValueError("unknown timeframe %r; cannot annualize" % (timeframe,))
    return SECONDS_PER_YEAR / seconds


class ContinuousBacktestPipeline:
    """Automated backtesting pipeline that runs on schedule."""

    def __init__(self, base_dir: str = ""):
        self.base_dir = base_dir or str(Path(__file__).parent.parent)
        self.artifacts_dir = os.path.join(self.base_dir, "artifacts", "backtest_results")
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.state_path = os.path.join(self.artifacts_dir, "pipeline_state.json")
        self.results_path = os.path.join(self.artifacts_dir, "pipeline_results.json")
        self.state = self._load_state()
        # Last run's params (used by the CPCV stage to replay the same data)
        self._last_timeframe = "H1"
        self._last_bars = 500

    def _load_state(self) -> Dict[str, Any]:
        """Load pipeline state from disk."""
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "last_run": None,
            "total_runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "last_symbol": None,
            "last_timeframe": None,
            "degradation_alerts": [],
        }

    def _save_state(self) -> None:
        """Save pipeline state to disk."""
        try:
            with open(self.state_path, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save pipeline state: %s", exc)

    def run_validation_cycle(
        self,
        symbol: str = "EURUSD",
        strategy_name: str = "default",
        timeframe: str = "H1",
        bars: int = 500,
        bars_df: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Entry point called by mt5_trader.py background validation loop.

        ``bars_df`` lets a caller that already fetched the OHLC window (e.g. a
        multi-strategy sweep) reuse the exact same bars for every strategy:
        one broker fetch, one identical evidence window, no per-strategy
        refetch drift.
        """
        res = self.run_full_validation(
            symbol=symbol,
            strategy_name=strategy_name,
            timeframe=timeframe,
            bars=bars,
            bars_df=bars_df,
            run_walkforward=True,
            run_dsr=True,
            compare_live=True,
        )
        res["strategy_id"] = strategy_name
        res["strategy_name"] = strategy_name
        try:
            from operations.event_bridge import emit as _eb_emit
            _eb_emit("VALIDATION_RESULT", {
                "strategy_id": strategy_name,
                "strategy_name": strategy_name,
                "symbol": symbol,
                "status": res.get("verdict", {}).get("status", "COMPLETE"),
                "passed": res.get("verdict", {}).get("passed", False),
                "sharpe": res.get("stages", {}).get("backtest", {}).get("sharpe", 0),
                "deflated_sharpe": res.get("stages", {}).get("dsr", {}).get("deflated_sharpe", 0),
            })
        except Exception:
            pass
        return res

    def run_full_validation(
        self,
        symbol: str = "EURUSD",
        strategy_name: str = "default",
        timeframe: str = "H1",
        bars: int = 500,
        run_walkforward: bool = True,
        run_dsr: bool = True,
        compare_live: bool = True,
        cost_overrides: Optional[Dict[str, Any]] = None,
        bars_df: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Run full validation suite for a symbol/timeframe."""
        log.info("=" * 60)
        log.info("Starting full validation: %s %s (%d bars, strategy=%s)",
                 symbol, timeframe, bars, strategy_name)
        log.info("=" * 60)
        self._last_timeframe = timeframe
        self._last_bars = bars

        start_time = time.time()
        results = {
            "symbol": symbol,
            "strategy_id": strategy_name,
            "strategy_name": strategy_name,
            "timeframe": timeframe,
            "bars": bars,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

        # Stage 1: Full Pipeline Backtest
        log.info("Stage 1: Full Pipeline Backtest")
        _regime_label = ""
        try:
            from data.market_data_provider import fetch_ohlc
            # fetch_ohlc() fails closed on its own (RuntimeError when no MT5
            # terminal is reachable in this process — see its _get_mt5()
            # fallback for engine-as-__main__ processes). Do not pre-check
            # sys.modules["mt5_trader"] here: it is always absent when the
            # engine runs as `python mt5_trader.py` and would block Stage 1
            # on every cycle.
            if bars_df is None:
                bars_df = fetch_ohlc(symbol, timeframe, max(bars, 500))
            if bars_df is None or len(bars_df) < 200:
                raise RuntimeError(
                    f"Backtest requires >=200 bars of {symbol} {timeframe}; "
                    f"got {None if bars_df is None else len(bars_df)}."
                )
            backtest_res = self._run_pipeline_backtest(
                symbol=symbol,
                strategy_name=strategy_name,
                bars_df=bars_df,
                timeframe=timeframe,
                cost_overrides=cost_overrides,
            )
            results["stages"]["backtest"] = backtest_res
            _regime_label = self._regime_from_bars(bars_df)
            results["market_regime"] = _regime_label
            log.info(
                "Stage 1 complete: sharpe=%.3f trades=%d regime=%s (data_source=%s)",
                backtest_res.get("sharpe", 0.0),
                backtest_res.get("total_trades", 0),
                _regime_label or "unknown",
                backtest_res.get("data_source", "unknown"),
            )
            # Feed the meta-loop corpus (Loop 4) so pattern mining has data.
            try:
                from core.meta_loop import get_meta_loop
                _ml = get_meta_loop()
                _ml.log_outcome(
                    hypothesis_id=str(strategy_name),
                    hypothesis_source="continuous_backtest_pipeline",
                    backtest_sharpe=float(backtest_res.get("sharpe", 0.0) or 0.0),
                    backtest_max_dd=float(backtest_res.get("max_drawdown", 0.0) or 0.0),
                    backtest_win_rate=float(backtest_res.get("win_rate", 0.0) or 0.0),
                    backtest_n_trades=int(backtest_res.get("total_trades", 0) or 0),
                    holding_horizon=str(timeframe or ""),
                    regime=_regime_label,
                )
            except Exception as _ml_exc:
                log.debug("Meta-loop log_outcome skipped: %s", _ml_exc)
        except Exception as exc:
            log.exception("Stage 1 failed: %s", exc)
            results["stages"]["backtest"] = {
                "status": "FAILED",
                "error": str(exc),
                "sharpe": 0.0,
                "executed": 0,
            }

        # Stage 2: Deflated Sharpe Ratio
        if run_dsr:
            log.info("Stage 2: Deflated Sharpe Ratio")
            try:
                dsr_stage = self._run_dsr_gate(
                    backtest_res=results.get("stages", {}).get("backtest", {}),
                    symbol=symbol,
                    timeframe=timeframe,
                )
                results["stages"]["dsr"] = dsr_stage
            except Exception as exc:
                log.exception("Stage 2 (DSR) failed: %s", exc)
                results["stages"]["dsr"] = {"status": "FAILED", "error": str(exc)}

        # Stage 3: Walk-Forward Analysis
        if run_walkforward:
            log.info("Stage 3: Walk-Forward Analysis")
            try:
                import research.research_validation as _rv
                import research.research_validation.labelled_dataset as _ld
            except Exception:
                try:
                    import research.validation as _rv
                    import research.validation.labelled_dataset as _ld
                except Exception:
                    _rv = None
                    _ld = None

            try:
                wfa_stage = self._run_walkforward(
                    symbol=symbol,
                    strategy_name=strategy_name,
                    timeframe=timeframe,
                    bars=bars,
                    cost_overrides=cost_overrides,
                )
                results["stages"]["walkforward"] = wfa_stage
                # Feed measured live evidence back to the meta-loop corpus.
                # Only broker-reconciled R-multiples that cleared the 20-sample
                # floor count; an insufficiency is not an outcome and is never
                # logged as one. This is the live half of the IS/OOS pair the
                # SOURCE_OVERFIT miner needs.
                try:
                    if str(wfa_stage.get("status", "")).upper() == "OK":
                        _wf_live_sharpe = wfa_stage.get("annualized_sharpe_20")
                        _wf_n = int(wfa_stage.get("r_multiples", 0) or 0)
                        if _wf_live_sharpe is not None and _wf_n >= 20:
                            from core.meta_loop import get_meta_loop
                            get_meta_loop().log_outcome(
                                hypothesis_id=str(strategy_name),
                                hypothesis_source="continuous_backtest_pipeline",
                                backtest_sharpe=float(
                                    results.get("stages", {}).get("backtest", {}).get("sharpe", 0.0) or 0.0
                                ),
                                live_sharpe=float(_wf_live_sharpe),
                                live_n_trades=_wf_n,
                                holding_horizon=str(timeframe or ""),
                                regime=_regime_label,
                            )
                except Exception as _mll_exc:
                    log.debug("Meta-loop live evidence feed skipped: %s", _mll_exc)
            except Exception as exc:
                log.exception("Stage 3 (walk-forward) failed: %s", exc)
                results["stages"]["walkforward"] = {
                    "status": "FAILED",
                    "error": str(exc),
                }

        # Stage 3b: CPCV (PBO/DSR) on net-of-cost returns — Stage-1 stream,
        # falling back to broker-reconciled journal R-multiples. Never a
        # silent skip: concrete verdict or concrete insufficiency.
        log.info("Stage 3b: CPCV (PBO/DSR)")
        try:
            results["stages"]["cpcv"] = self._stage_cpcv(
                symbol=symbol,
                strategy_name=strategy_name,
                backtest_stage=results.get("stages", {}).get("backtest", {}),
            )
        except Exception as exc:
            log.exception("Stage 3b (CPCV) failed: %s", exc)
            results["stages"]["cpcv"] = {"status": "ERROR", "error": str(exc)}

        # Stage 4: Live-vs-Backtest Comparison
        if compare_live:
            log.info("Stage 4: Live-vs-Backtest Comparison")
            try:
                live_stage = self._compare_live(
                    symbol=symbol,
                    strategy_name=strategy_name,
                    backtest_res=results.get("stages", {}).get("backtest", {}),
                )
                results["stages"]["live_comparison"] = live_stage
            except Exception as exc:
                log.exception("Stage 4 (live comparison) failed: %s", exc)
                results["stages"]["live_comparison"] = {
                    "status": "FAILED",
                    "error": str(exc),
                }

        # Stage 5: Live Validation Gate (composite: WFE + overfit + drawdown + win rate)
        log.info("Stage 5: Live Validation Gate")
        try:
            _bt = results.get("stages", {}).get("backtest", {})
            _wf = results.get("stages", {}).get("walkforward", {})
            _returns = _bt.get("r_multiples") or []
            _metrics = {
                "max_drawdown_pct": _bt.get("max_drawdown", 0.0),
                "win_rate": _bt.get("win_rate", 0.0),
            }
            lv_stage = self._run_live_validation_gate(
                strategy_id=strategy_name, returns=_returns, metrics=_metrics,
            )
            results["stages"]["live_validation"] = lv_stage
        except Exception as exc:
            log.exception("Stage 5 (live validation gate) failed: %s", exc)
            results["stages"]["live_validation"] = {"status": "FAILED", "error": str(exc)}

        elapsed = time.time() - start_time
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration_seconds"] = round(elapsed, 3)
        results["verdict"] = self._compute_verdict(results["stages"])
        self._save_results(results)

        log.info(
            "Full validation complete: %s %s (%ds) verdict=%s passed=%s",
            symbol,
            timeframe,
            round(elapsed, 1),
            results["verdict"].get("status", "UNKNOWN"),
            results["verdict"].get("passed", False),
        )
        self.state["last_run"] = results["completed_at"]
        self.state["total_runs"] += 1
        if results["verdict"].get("passed", False):
            self.state["successful_runs"] += 1
        else:
            self.state["failed_runs"] += 1
        self._save_state()
        return results

    # ------------------------------------------------------------------
    # Sub-stages
    # ------------------------------------------------------------------

    @staticmethod
    def _regime_from_bars(bars_df: Any) -> str:
        """Measured market-regime label for the evidence window.

        Uses the existing RegimeDetectionEngine (Hurst + ATR percentile) on the
        exact bars the backtest consumed. Missing/short data returns "" rather
        than a fabricated regime, so the meta-loop only groups labelled rows.
        """
        try:
            if bars_df is None or len(bars_df) < 30:
                return ""
            closes = [float(x) for x in bars_df["close"].tolist()]
            highs = bars_df["high"].astype(float)
            lows = bars_df["low"].astype(float)
            atr_series = (highs - lows).rolling(14).mean().dropna().tolist()
            if not closes or not atr_series:
                return ""
            from core.hierarchical_portfolio import RegimeDetectionEngine
            state = RegimeDetectionEngine().detect_regime(
                closes,
                current_atr=float(atr_series[-1]),
                atr_history=atr_series,
            )
            return str(getattr(state, "primary_regime", "") or "")
        except Exception:
            return ""

    def _run_pipeline_backtest(
        self,
        symbol: str,
        strategy_name: str,
        bars_df: Any,
        timeframe: str = "H1",
        cost_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run the indicator -> gates -> execution backtest on the supplied bars.

        Wires the research engine's autonomous backtest entry point, which is the
        function actually present on MT5BrokerBacktestEngine (run_full_autonomous_backtest).
        The previous stub called a non-existent ``run_backtest`` and would have
        failed at runtime even after the module compiled.
        """
        try:
            from research.mt5_backtest_engine import MT5BrokerBacktestEngine
        except Exception:
            from mt5_backtest_engine import MT5BrokerBacktestEngine  # type: ignore[no-redef]

        strategy_id = str(strategy_name)
        engine = MT5BrokerBacktestEngine()
        # Per-strategy registry wiring (PU-0156 §8.1): a registered label runs
        # its OWN causal entry signal through the engine's external-signal mode,
        # so its stage-1 metrics describe that hypothesis, not the generic EMA
        # crossover. The builder is invoked inside the engine on exactly the
        # bars the engine will trade (score/bar alignment is guaranteed), and a
        # failing or misaligned builder fails the stage loudly — never silently
        # mislabeled as the generic ruleset. Unregistered labels ("default" and
        # any non-registry name) keep the generic path, logged explicitly.
        signal_builder: Optional[Callable[[Any], Any]] = None
        try:
            from research.strategy_signal_registry import build_signal, has_signal
        except ImportError:  # pragma: no cover - flat-layout fallback
            from strategy_signal_registry import build_signal, has_signal
        if has_signal(strategy_id):
            def signal_builder(bdf: Any) -> Any:
                return build_signal(
                    strategy_id,
                    bdf["close"].to_numpy(dtype=np.float64),
                    bdf["high"].to_numpy(dtype=np.float64),
                    bdf["low"].to_numpy(dtype=np.float64),
                )
        elif strategy_id != "default":
            log.warning(
                "Backtest for '%s': no signal in strategy_signal_registry — running "
                "the generic ruleset (metrics describe the crossover ruleset, "
                "NOT this label).", strategy_id,
            )
        res = engine.run_full_autonomous_backtest(
            symbol=symbol,
            strategy_name=strategy_id,
            timeframe=timeframe,
            count=len(bars_df) if hasattr(bars_df, "__len__") else 500,
            fast_period=12,
            slow_period=26,
            sl_pips=30.0,
            tp_pips=60.0,
            signal_builder=signal_builder,
            entry_signal_threshold=1.0,
            bars_df=bars_df,
        )
        if not isinstance(res, dict):
            res = {"status": "OK", "result": res}
        # The engine reports its metrics nested under ``metrics`` (sharpe_ratio,
        # win_rate, profit_factor, expectancy_r, max_drawdown_pct, total_trades)
        # while the pipeline stages (DSR gate, verdict, CPCV) read a flat schema
        # (sharpe, win_rate, profit_factor, expectancy_r, max_drawdown,
        # total_trades, n_obs, n_trades).  Without this normalization every
        # stage silently saw zeros and verdict.passed could never be True even
        # on a healthy backtest (discovered 2026-10-18 via the long gauntlet).
        metrics = res.get("metrics") if isinstance(res.get("metrics"), dict) else {}
        bars_count = len(bars_df) if hasattr(bars_df, "__len__") else 0
        trades = int(metrics.get("total_trades", 0) or 0)
        strategy_improvement = res.get("strategy_improvement") if isinstance(res.get("strategy_improvement"), dict) else {}
        dsr_gate = strategy_improvement.get("dsr_gate") if isinstance(strategy_improvement.get("dsr_gate"), dict) else {}
        n_trials = int(dsr_gate.get("n_trials", trades) or trades)
        _rm_raw = res.get("r_multiples") or metrics.get("trades") or metrics.get("r_multiples") or []
        _r_mults: List[float] = []
        for _t in _rm_raw:
            _rm = _t.get("r_multiple") if isinstance(_t, dict) else _t
            if _rm is None:
                continue
            try:
                _r_mults.append(float(_rm))
            except (TypeError, ValueError):
                continue
        r_multiples = _r_mults if _r_mults else None
        normalized = {
            "status": res.get("status", "COMPLETED"),
            "sharpe": float(metrics.get("sharpe_ratio", 0.0) or 0.0),
            "win_rate": float(metrics.get("win_rate", 0.0) or 0.0),
            "profit_factor": float(metrics.get("profit_factor", 0.0) or 0.0),
            "expectancy_r": float(metrics.get("expectancy_r", 0.0) or 0.0),
            "max_drawdown": float(metrics.get("max_drawdown_pct", 0.0) or 0.0),
            "total_trades": trades,
            "n_trades": trades,
            "n_trials": n_trials,
            "n_obs": int(bars_count or trades or 0),
            "strategy_name": strategy_id,
            "data_source": res.get("data_source", "unknown"),
            "r_multiples": r_multiples,
            "trades": res.get("trades") or metrics.get("trades") or [],
            "_raw": res,
        }
        return normalized

    def _run_dsr_gate(
        self,
        backtest_res: Dict[str, Any],
        symbol: str,
        timeframe: str,
    ) -> Dict[str, Any]:
        """Deflated Sharpe Ratio gate for the stage-1 equity curve."""
        sharpe = float(backtest_res.get("sharpe", 0.0) or 0.0)
        n_obs = int(backtest_res.get("n_obs", 0) or 0)
        # Prefer the engine's cumulative trial count (n_trials) for the
        # deflated-sharpe family size; fall back to the trade count.  Using
        # the trade count alone made the gate permanently fail (discovered
        # 2026-10-18: deflated_sharpe always 0.00 with k=4).
        k = int(backtest_res.get("n_trials", backtest_res.get("n_trades", 0)) or 0)
        if n_obs <= 0 or k <= 0:
            return {"status": "SKIPPED", "reason": "insufficient observations", "deflated_sharpe": 0.0}

        # Tiny-sample floor (run-level): a deflated Sharpe computed over a
        # handful of executed trades has no statistical meaning — a 4-trade
        # annualized Sharpe of ~14 cleared the compound 'deflated >= 0.95'
        # comparison and stamped the whole validation as PASSED. The floor
        # is decided on what THIS run actually traded (total_trades), not on
        # the ledger-wide multiple-testing family (n_trials). FAILED — not
        # SKIPPED — because the stage ran and measured a real breach; a
        # skipped gate also blocks, but here the evidence is conclusive.
        try:
            from research.backtest_dsr_integrator import MIN_TRADES_FOR_DSR
        except ImportError:  # pragma: no cover - flat-layout fallback
            from backtest_dsr_integrator import MIN_TRADES_FOR_DSR
        _run_trades = int(backtest_res.get("total_trades", backtest_res.get("n_trades", 0)) or 0)
        if _run_trades < MIN_TRADES_FOR_DSR:
            return {
                "status": "FAILED",
                "reason": "insufficient_trades",
                "n_trades": _run_trades,
                "min_trades_required": MIN_TRADES_FOR_DSR,
                "sharpe": sharpe,
                "deflated_sharpe": 0.0,
                "passed": False,
            }

        periods = _periods_per_year(timeframe)
        if periods <= 0:
            return {"status": "SKIPPED", "reason": "cannot annualize timeframe", "deflated_sharpe": 0.0}

        try:
            from core.trial_ledger import calculate_deflated_sharpe_ratio as _calc_dsr
            from scipy import stats as _stats
        except Exception:
            from core.trial_ledger import calculate_deflated_sharpe_ratio as _calc_dsr  # type: ignore[no-redef]
            from scipy import stats as _stats

        # Prefer the engine's own DSR gate result (authoritative deflated
        # sharpe + pass flag) over recomputing a p-value here; the engine
        # already applies the right trial count and multiple-testing
        # correction.  Fall back to the p-value path only when the engine
        # result is unavailable (legacy/test fixtures).
        raw = backtest_res.get("_raw") if isinstance(backtest_res.get("_raw"), dict) else {}
        si = raw.get("strategy_improvement") if isinstance(raw.get("strategy_improvement"), dict) else {}
        engine_dsr_gate = si.get("dsr_gate") if isinstance(si.get("dsr_gate"), dict) else {}
        engine_dsr = engine_dsr_gate.get("dsr") if isinstance(engine_dsr_gate, dict) else None
        engine_passed = engine_dsr_gate.get("passed") if isinstance(engine_dsr_gate, dict) else None

        if engine_dsr is not None:
            deflated = float(engine_dsr)
            passed = bool(engine_passed) if engine_passed is not None else (deflated >= 0.95)
        else:
            deflated = _calc_dsr(
                estimated_sharpe=sharpe,
                num_trials=max(1, k),
                sample_size=max(1, n_obs),
                skewness=0.0,
                kurtosis=3.0,
                benchmark_sharpe=0.0,
            )
            passed = bool(deflated < 0.05)

        return {
            "status": "OK",
            "sharpe": sharpe,
            "deflated_sharpe": float(deflated),
            "n_obs": n_obs,
            "n_trades": k,
            "periods_per_year": periods,
            "p_h0": 0.05,
            "passed": passed,
        }

    def _run_walkforward(
        self,
        symbol: str,
        strategy_name: str,
        timeframe: str,
        bars: int,
        cost_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Walk-forward / CPCV stage with journal-backed R-multiple reporting."""
        from pathlib import Path

        journal_dir = Path(self.base_dir) / "data" / "journals"
        candidate = None
        if journal_dir.is_dir():
            for p in sorted(journal_dir.glob(f"{symbol}_*.db"), reverse=True):
                candidate = str(p)
                break
        if candidate is None:
            # The live engine journals to the root trading_state.db; the
            # journals dir only exists for sandboxed runs.
            root_journal = Path(self.base_dir) / "trading_state.db"
            if root_journal.is_file():
                candidate = str(root_journal)
        if candidate is None:
            return {
                "status": "INSUFFICIENT_JOURNAL_EVIDENCE",
                "journal": None,
                "samples": 0,
                "reason": "no journal found for %s" % symbol,
            }

        for db_path in (
            candidate,
            str(journal_dir / f"{symbol}_walkforward.db"),
            str(journal_dir / f"{symbol}_backtest.db"),
        ):
            db_path_obj = Path(db_path)
            if not db_path_obj.is_file():
                continue
            tickets = _broker_confirmed_tickets(db_path)
            multiples = _broker_reconciled_r_multiples(db_path)

            last_20 = multiples[-20:]
            last_40 = multiples[-40:]
            floor_20_ok = len(last_20) >= 20
            floor_40_ok = len(last_40) >= 40

            if len(multiples) < 20:
                log.warning(
                    "Walk-forward: fewer than 20 broker-reconciled R-multiples in %s "
                    "(tickets=%d, multiples=%d, last20=%d)",
                    db_path,
                    len(tickets),
                    len(multiples),
                    len(last_20),
                )
                return {
                    "status": "INSUFFICIENT_JOURNAL_EVIDENCE",
                    "journal": db_path,
                    "broker_tickets": len(tickets),
                    "samples": len(multiples),
                    "reason": (
                        "needs >= 20 broker-reconciled R-multiples for "
                        "walk-forward stats (found %d)" % len(multiples)
                    ),
                }

            def _sharpe_from(items: List[float]) -> Optional[float]:
                if len(items) < 2:
                    return None
                try:
                    from scipy import stats as _sf_stats
                except Exception:
                    from scipy import stats as _sf_stats  # type: ignore[no-redef]
                mean = sum(items) / len(items)
                var = sum((x - mean) ** 2 for x in items) / (len(items) - 1)
                std = var ** 0.5
                if std == 0:
                    return None
                try:
                    r = _sf_stats.ttest_1samp(items, 0.0)
                    if r.pvalue is None:
                        return None
                except Exception:
                    pass
                sem = std / (len(items) ** 0.5)
                return mean / sem

            def _annualized_from(items: List[float]) -> Optional[float]:
                """Same normalisation as the Stage-1 engine Sharpe.

                The engine reports mean(R)/std(R) * sqrt(252); the t-stat above
                is not comparable to it. Meta IS/OOS comparisons use this one.
                """
                if len(items) < 2:
                    return None
                mean = sum(items) / len(items)
                var = sum((x - mean) ** 2 for x in items) / (len(items) - 1)
                std = var ** 0.5
                if std == 0:
                    return None
                return mean / std * (252 ** 0.5)

            wf_sharpe = _sharpe_from(last_20)
            robust_sharpe = _sharpe_from(last_40)
            annualized_20 = _annualized_from(last_20)
            annualized_40 = _annualized_from(last_40)

            return {
                "status": "OK",
                "journal": db_path,
                "broker_tickets": len(tickets),
                "r_multiples": len(multiples),
                "floor_20": {"met": floor_20_ok, "count": len(last_20)},
                "floor_40": {"met": floor_40_ok, "count": len(last_40)},
                "reported_walkforward_sharpe": wf_sharpe,
                "robust_sharpe_40": robust_sharpe,
                "annualized_sharpe_20": annualized_20,
                "annualized_sharpe_40": annualized_40,
                "mean_r": float(sum(multiples) / len(multiples)) if multiples else 0.0,
                "note": (
                    "R-multiples sourced through labelled_dataset; the previous "
                    "reader queried the non-existent trades.r_multiple column. "
                    "reported_walkforward_sharpe is a t-stat; annualized_* use "
                    "the engine's mean/std*sqrt(252) convention."
                ),
            }

        return {
            "status": "FAILED",
            "reason": "no readable journal for %s" % symbol,
        }

    def _stage_cpcv(
        self,
        symbol: str,
        strategy_name: str,
        backtest_stage: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run CPCV (PBO/DSR) on net-of-cost returns and record it honestly.

        Feed priority: (1) the Stage-1 backtest's own net-of-cost R-multiple
        stream, (2) live trade R-multiples from trading_state.db. If neither
        has enough samples, the concrete insufficiency is reported — the
        stage is never silently skipped.
        """
        from core.cpcv_backtester import run_combinatorial_purged_backtest

        r_multiples = None
        source = "backtest_trades"
        # Priority 1 (per this stage's contract): the Stage-1 backtest's OWN
        # net-of-cost R-multiple stream — the strategy actually under test.
        # A previous implementation re-ran a generic-ruleset backtest here,
        # so every label's CPCV/PBO scored the same deterministic_signal
        # stream regardless of the strategy (PU-0156 finding: all 14 sweep
        # labels shared one identical CPCV stream). A stream scored under a
        # different ruleset must never be reported as this strategy's CPCV.
        stage_stream = backtest_stage.get("r_multiples")
        if isinstance(stage_stream, (list, tuple)) and len(stage_stream) >= 40:
            try:
                r_multiples = [float(r) for r in stage_stream]
            except (TypeError, ValueError):
                r_multiples = None

        if not r_multiples or len(r_multiples) < 40:
            _samples = len(r_multiples or [])
            log.info(
                "Stage 3b CPCV not run: only %d net-of-cost %s returns available (need >= 40 for combinatorial paths; journal fallback disabled for strategy isolation)",
                _samples, source,
            )
            return {
                "status": "INSUFFICIENT_SAMPLES_FOR_CPCV",
                "source": source,
                "samples": _samples,
                "reason": "needs >= 40 net-of-cost backtest trade returns for combinatorial paths (journal fallback disabled)",
                "is_statistically_robust": False,
            }

        cpcv = run_combinatorial_purged_backtest(
            returns_series=r_multiples,
            n_splits=6,
            n_test_groups=2,
        )
        cpcv["source"] = source
        cpcv["samples"] = len(r_multiples)
        _mean_sr = float(cpcv.get("mean_cpcv_sharpe", 0))
        _dsr = float(cpcv.get("deflated_sharpe_ratio", 0))
        _pbo = float(cpcv.get("probability_of_backtest_overfitting_pbo", 1.0))
        log.info(
            "Stage 3b CPCV (%s, n=%d): mean_sharpe=%.3f DSR_conf=%.3f PBO=%.3f -> %s",
            source, len(r_multiples), _mean_sr, _dsr, _pbo, cpcv.get("status", "?"),
        )
        try:
            from operations.event_bridge import emit as _cpcv_emit
            _cpcv_emit("CPCV_RESULT", {
                "symbol": symbol,
                "strategy": strategy_name,
                "source": source,
                "samples": len(r_multiples),
                "mean_sharpe": round(_mean_sr, 4),
                "deflated_sharpe": round(_dsr, 4),
                "pbo": round(_pbo, 4),
                "is_statistically_robust": cpcv.get("is_statistically_robust"),
                "status": cpcv.get("status"),
            }, correlation_id="continuous-backtest")
        except Exception:
            pass
        return cpcv

    def _compare_live_vs_backtest(
        self,
        symbol: str,
        backtest_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare live/demo performance against backtest expectations.

        Admission is delegated to ``labelled_dataset``: the same independent
        broker evidence *and* synthetic/fixture exclusion that gates
        walk-forward and CPCV.  A direct SQL read here previously admitted the
        50 backtest-fixture rows (payload.source="backtest") as live trades
        and reported a divergence verdict from them.
        """
        try:
            db_path = os.path.join(self.base_dir, "trading_state.db")
            if not os.path.exists(db_path):
                return {"status": "skipped", "reason": "no_trading_db"}

            try:
                from research.research_validation import labelled_dataset
            except ImportError:
                from research_validation import labelled_dataset
            dataset = labelled_dataset(db_path)
            rows = [
                r for r in dataset.get("rows", [])
                if str(r.get("symbol") or "").upper() == str(symbol or "").upper()
            ][-50:]

            if len(rows) < 10:
                return {
                    "status": "skipped",
                    "reason": "insufficient_live_trades",
                    "count": len(rows),
                    "excluded": dataset.get("excluded", {}),
                }

            # Compute live metrics from admitted, broker-reconciled R-multiples.
            live_r_multiples = [float(r["r_multiple"]) for r in rows]
            live_win_rate = sum(1 for r in live_r_multiples if r > 0) / len(live_r_multiples)
            live_avg_r = sum(live_r_multiples) / len(live_r_multiples)

            # Compare with backtest
            bt_win_rate = backtest_results.get("win_rate", 0.5)
            bt_expectancy = backtest_results.get("expectancy_r", 0)

            # Compute divergence scores
            win_rate_divergence = abs(live_win_rate - bt_win_rate) / max(bt_win_rate, 0.01)
            expectancy_divergence = abs(live_avg_r - bt_expectancy) / max(abs(bt_expectancy), 0.01)

            # Flag if divergence is significant (>20%)
            significant = win_rate_divergence > 0.2 or expectancy_divergence > 0.2

            return {
                "status": "completed",
                "live_trades": len(rows),
                "live_win_rate": round(live_win_rate, 4),
                "live_avg_r": round(live_avg_r, 4),
                "backtest_win_rate": bt_win_rate,
                "backtest_expectancy": bt_expectancy,
                "win_rate_divergence": round(win_rate_divergence, 4),
                "expectancy_divergence": round(expectancy_divergence, 4),
                "significant_divergence": significant,
                "verdict": "DIVERGENT" if significant else "ALIGNED",
            }

        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def _run_live_validation_gate(
        self,
        strategy_id: str,
        returns: List[float],
        metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Composite live-deployment gate (WFE + overfit + drawdown + win rate).

        Wraps core.live_training_infrastructure.LiveValidationGate so the
        pipeline has a single pass/fail verdict on whether a strategy is
        safe to deploy live.  Fail-closed: missing returns -> NOT_VALIDATED.
        """
        try:
            from core.live_training_infrastructure import LiveValidationGate
        except Exception as exc:
            return {"status": "SKIPPED", "reason": "LiveValidationGate unavailable: %s" % exc}
        gate = LiveValidationGate(store=None)
        return gate.validate(strategy_id=strategy_id, returns=returns or [], metrics=metrics or {})

    def _compare_live(
        self,
        symbol: str,
        strategy_name: str,
        backtest_res: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Real live-vs-backtest comparison via broker-reconciled closes."""
        return self._compare_live_vs_backtest(symbol, backtest_res)

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------

    def _compute_verdict(self, stages: Dict[str, Any]) -> Dict[str, Any]:
        """Compute overall validation verdict."""
        blockers = []
        warnings = []

        # Accept both the bare stage-map (engine path) and the full results
        # wrapper ({"stages": {...}}) — callers pass either. Without this,
        # a wrapper call saw an empty stage map and always reported
        # backtest_failed.
        if isinstance(stages, dict) and isinstance(stages.get("stages"), dict):
            stages = stages["stages"]

        bt = stages.get("backtest", {}) if isinstance(stages, dict) else {}

        # Synthetic fixture data (seeded random walk) is NEVER authoritative
        # for promotion — it is a CI/testing convenience, not market evidence.
        if bt.get("data_source") == "synthetic":
            blockers.append("synthetic_data_not_authoritative")

        # Check backtest. Case-insensitive: the engine reports "COMPLETED"
        # and the normalized stage carries it verbatim; comparing lowercase
        # alone dead-wired every real verdict to FAIL (blockers=
        # ['backtest_failed']) regardless of metric quality.
        bt_status = str(bt.get("status", "")).upper()
        if bt_status not in ("COMPLETED", "OK"):
            blockers.append("backtest_failed")
        else:
            # Gate the metrics the payload actually carries. A real
            # engine stage always includes them; a legacy/minimal fixture
            # that omits one is not evidence of a bad value — treating a
            # missing number as a zero blocked every minimal payload
            # (profit_factor/expectancy_r absent -> automatic FAIL).
            if bt.get("sharpe", 0) < 1.0:
                blockers.append("sharpe_below_1")
            pf = bt.get("profit_factor", None)
            ex = bt.get("expectancy_r", None)
            wr = bt.get("win_rate", None)
            if pf is not None and pf < 1.2:
                blockers.append("profit_factor_below_1.2")
            if ex is not None and ex <= 0:
                blockers.append("negative_expectancy")
            if wr is not None and wr < 0.45:
                warnings.append("low_win_rate")

        # Trade count floor: a run with fewer than 30 trades cannot prove edge
        total_trades = bt.get("total_trades", bt.get("n_trades", None))
        if total_trades is not None and total_trades < 30:
            blockers.append("insufficient_trades_sample")

        # Check DSR (fail-closed)
        dsr = stages.get("dsr", {}) if isinstance(stages, dict) else {}
        dsr_status = str(dsr.get("status", "")).upper()
        if dsr_status == "FAILED":
            blockers.append("dsr_gate_failed")
        elif dsr_status == "SKIPPED":
            blockers.append("dsr_gate_skipped")
        elif dsr_status in ("OK", "COMPLETED") and dsr.get("passed", None) is False:
            blockers.append("dsr_gate_failed")

        # Check CPCV & PBO (fail-closed)
        cpcv = stages.get("cpcv", {}) if isinstance(stages, dict) else {}
        cpcv_status = str(cpcv.get("status", "")).upper()
        if cpcv_status == "FAILED":
            blockers.append("cpcv_stage_failed")
        elif cpcv_status in ("SKIPPED", "INSUFFICIENT_SAMPLES_FOR_CPCV"):
            blockers.append("cpcv_stage_skipped")
        elif cpcv_status == "OVERFITTING_RISK_DETECTED":
            blockers.append("cpcv_overfitting_risk_detected")
        elif cpcv_status == "CPCV_VALIDATED":
            pbo = cpcv.get("probability_of_backtest_overfitting_pbo", 0.0)
            if pbo > 0.25:
                blockers.append("pbo_above_threshold")
            if cpcv.get("is_statistically_robust") is False:
                blockers.append("cpcv_not_statistically_robust")

        # Check walk-forward (fail-closed on failure / missing evidence)
        wf = stages.get("walkforward", {}) if isinstance(stages, dict) else {}
        wf_status = str(wf.get("status", "")).upper()
        if wf_status == "FAILED":
            blockers.append("walkforward_failed")
        elif wf_status in ("SKIPPED", "INSUFFICIENT_JOURNAL_EVIDENCE"):
            blockers.append("walkforward_skipped")
        elif wf_status in ("OK", "COMPLETED"):
            if wf.get("stability_score", 0) < 0.5:
                warnings.append("low_stability_score")

        # Check live comparison
        lc = stages.get("live_comparison", {}) if isinstance(stages, dict) else {}
        if str(lc.get("status", "")).upper() in ("OK", "COMPLETED") and lc.get("significant_divergence", False):
            blockers.append("live_backtest_divergence")

        passed = len(blockers) == 0
        return {
            "passed": passed,
            "status": "PASSED" if passed else "FAILED",
            "blockers": blockers,
            "warnings": warnings,
        }
    def _save_results(self, results: Dict[str, Any]) -> None:
        """Append run results to the results fan-in file.

        /api/backtests and research.divergence_monitor read
        artifacts/backtest_results/pipeline_results.json (4-source fan-in);
        without this write the pipeline's contribution goes stale.

        Writes are atomic: a previous run left a 0-byte file behind when the
        process died between truncate and dump, and every later append then
        failed silently on the JSON load. A corrupt/empty file starts a fresh
        history instead of freezing the fan-in forever.
        """
        try:
            history: List[Any] = []
            if os.path.exists(self.results_path) and os.path.getsize(self.results_path) > 0:
                try:
                    with open(self.results_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        history = loaded
                    elif isinstance(loaded, dict):
                        history = [loaded]
                    else:
                        log.warning("Pipeline results file had unexpected shape %s; starting fresh", type(loaded).__name__)
                except (json.JSONDecodeError, OSError) as parse_exc:
                    log.warning("Pipeline results file unreadable (%s); starting fresh", parse_exc)
            history.append(results)
            # Keep last 100 results
            history = history[-100:]
            tmp_path = self.results_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.results_path)
        except Exception as exc:
            log.warning("Failed to save results: %s", exc)


# ------------------------------------------------------------------
# Module-level entry points used by mt5_trader.py
# ------------------------------------------------------------------


def run_continuous(
    symbol: str = "EURUSD",
    strategy_name: str = "default",
    timeframe: str = "H1",
    bars: int = 500,
    interval_seconds: int = 14400,
    max_cycles: Optional[int] = None,
) -> None:
    """Background validation loop: run a full validation cycle on an interval.

    Plymouth-Q1 evaluated this as a job definition, not a function body, so
    the signature is stable but the implementation is intentionally a no-op
    scaffold: the real eventing lives in mt5_trader.py's background loop and
    the event bus.  Calling this direct path is useful for tests and local
    smoke runs only.
    """
    log.info(
        "run_continuous scaffold: %s %s (%d bars, every %ds)",
        symbol,
        timeframe,
        bars,
        interval_seconds,
    )
    pipeline = ContinuousBacktestPipeline()
    cycle = 0
    while True:
        try:
            res = pipeline.run_full_validation(
                symbol=symbol,
                strategy_name=strategy_name,
                timeframe=timeframe,
                bars=bars,
                run_walkforward=True,
                run_dsr=True,
                compare_live=True,
            )
            log.info(
                "cycle=%d verdict=%s passed=%s",
                cycle,
                res.get("verdict", {}).get("status", "UNKNOWN"),
                res.get("verdict", {}).get("passed", False),
            )
        except Exception:
            log.exception("validation cycle %d failed", cycle)
        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Continuous Backtest Pipeline")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--strategy", default="default")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--bars", type=int, default=500)
    parser.add_argument("--interval", type=int, default=14400)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.once:
        p = ContinuousBacktestPipeline()
        r = p.run_full_validation(
            symbol=args.symbol,
            strategy_name=args.strategy,
            timeframe=args.timeframe,
            bars=args.bars,
            run_walkforward=True,
            run_dsr=True,
            compare_live=False,
        )
        print(json.dumps(r, indent=2, default=str))
    else:
        run_continuous(
            symbol=args.symbol,
            strategy_name=args.strategy,
            timeframe=args.timeframe,
            bars=args.bars,
            interval_seconds=args.interval,
        )

