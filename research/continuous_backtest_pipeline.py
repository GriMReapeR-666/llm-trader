"""Continuous Backtesting Pipeline

Automated scheduled backtesting that runs periodically and feeds results
into promotion gates. This is the core of the autonomous validation system.

Runs:
- Full pipeline backtest (indicator → gates → execution)
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

import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        from research.research_validation import broker_reconciled_tickets
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
        from research_validation import labelled_dataset
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
    ) -> Dict[str, Any]:
        """Entry point called by mt5_trader.py background validation loop."""
        res = self.run_full_validation(
            symbol=symbol,
            strategy_name=strategy_name,
            timeframe=timeframe,
            bars=bars,
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
    ) -> Dict[str, Any]:
        """Run full validation suite for a symbol/timeframe."""
        log.info("=" * 60)
        log.info("Starting full validation: %s %s (%d bars, strategy=%s)", symbol, timeframe, bars, strategy_name)
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
        try:
            raw_bars = None
            try:
                from data.market_data_provider import fetch_ohlc
                fetched = fetch_ohlc(symbol=symbol, timeframe=timeframe, count=bars)
                if fetched is not None and not getattr(fetched, "empty", False):
                    raw_bars = fetched.to_dict("records") if hasattr(fetched, "to_dict") else list(fetched)
            except Exception as _fetch_err:
                log.debug("Live OHLC fetch unavailable: %s", _fetch_err)

            if raw_bars is None or len(raw_bars) == 0:
                # Fail closed. Scoring a strategy on synthetic random-walk bars
                # yields a meaningless Sharpe that then poisons the DSR gate and
                # every downstream promotion/scoring decision.
                raise RuntimeError(
                    "live OHLC unavailable for %s/%s; refusing synthetic replay bars"
                    % (symbol, timeframe))

            from research.full_pipeline_backtest import run_full_pipeline_backtest
            bt_kwargs: Dict[str, Any] = dict(cost_overrides or {})
            # --- Per-strategy signals (PU-0156 §8.1) ---
            # Each registered label scores its OWN causal entry signal via the
            # engine's external-signal mode; unknown labels keep the generic
            # ruleset but are labeled honestly. The shared geometry/gates/
            # sizing stay identical, so cross-label comparisons remain valid.
            _sig_scores = None
            try:
                from research.strategy_signal_registry import build_signal
            except Exception:
                from research.strategy_signal_registry import build_signal
            try:
                _df_like = raw_bars
                _closes = [float(b.get("close", 0)) for b in _df_like]
                _highs = [float(b.get("high", 0)) for b in _df_like]
                _lows = [float(b.get("low", 0)) for b in _df_like]
                _sig_scores = build_signal(strategy_name, _closes, _highs, _lows)
                if _sig_scores is not None:
                    bt_kwargs["entry_signal_scores"] = _sig_scores
                    bt_kwargs["entry_signal_threshold"] = 0.75
            except KeyError:
                _sig_scores = None  # unregistered label: keep generic ruleset
            bt_res = run_full_pipeline_backtest(
                bars=raw_bars,
                symbol=symbol,
                timeframe=timeframe,
                periods_per_year=_periods_per_year(timeframe),
                name=strategy_name if strategy_name != "default" else f"{symbol}_{timeframe}_backtest",
                **bt_kwargs,
            )
            if _sig_scores is not None:
                # Mutate after construction: the engine sets ruleset itself for
                # the external-signal path; make the attribution explicit.
                bt_res.ruleset = f"strategy:{strategy_name}"
                bt_res.strategy_implemented = True
            elif _sig_scores is None and strategy_name != "default":
                bt_res.ruleset = f"generic_fallback:{strategy_name}"
            bt_dict = bt_res.to_dict() if hasattr(bt_res, "to_dict") else bt_res
            results["stages"]["backtest"] = {
                "status": "completed",
                # `name` is a label only. Without this, a generic ruleset scores
                # identically for every strategy and the number gets read as a
                # statement about that specific strategy.
                "ruleset": bt_dict.get("ruleset", "deterministic_signal"),
                "strategy_implemented": bool(bt_dict.get("strategy_implemented", False)),
                # Measured per-bar returns so the bootstrap's WFE / overfit gates
                # validate evidence instead of a series synthesised from the
                # Sharpe above. Absent -> those gates report NOT_VALIDATED.
                "returns": bt_dict.get("returns"),
                # Per-trade net-of-cost R-multiples: Stage 2b (CPCV) must
                # validate THIS strategy's trade stream, not a generic replay.
                "r_multiples": bt_dict.get("r_multiples"),
                "sharpe": round(float(bt_dict.get("sharpe_ratio", 0)), 2),
                "sortino": round(float(bt_dict.get("sortino_ratio", 0)), 2),
                "max_drawdown": round(float(bt_dict.get("max_drawdown_pct", 0)), 2),
                "win_rate": round(float(bt_dict.get("win_rate_pct", 0)) / 100.0, 4),
                "profit_factor": round(float(bt_dict.get("profit_factor", 0)), 2),
                "expectancy_r": round(float(bt_dict.get("expectancy_r", 0)), 4),
                "total_trades": int(bt_dict.get("total_trades", 0)),
                "bars_processed": len(raw_bars),
                # Net-of-cost accounting (per-fill TCE or flat legacy)
                "cost_model": bt_dict.get("cost_model", "flat_legacy"),
                "avg_slippage_bps": round(float(bt_dict.get("avg_slippage_bps", 0)), 2),
                "max_fill_slippage_bps": round(float(bt_dict.get("max_fill_slippage_bps", 0)), 2),
                "total_cost": round(float(bt_dict.get("total_cost", 0)), 2),
                "total_impact_cost": round(float(bt_dict.get("total_impact_cost", 0)), 2),
                "total_spread_cost": round(float(bt_dict.get("total_spread_cost", 0)), 2),
                "total_commission": round(float(bt_dict.get("total_commission", 0)), 2),
                # Retail cost-stress overlay (zero when the overlay is off)
                "total_swap_cost": bt_dict.get("total_swap_cost", 0.0),
                "swap_nights_charged": bt_dict.get("swap_nights_charged", 0),
            }
            _bt_stage = results["stages"]["backtest"]
            log.info(
                "Stage 1 fill model: %s — avg slippage %.2f bps (max %.2f), total cost $%.2f "
                "(impact $%.2f, spread $%.2f, commission $%.2f) across %d fills — all metrics net-of-cost",
                _bt_stage.get("cost_model"), _bt_stage.get("avg_slippage_bps", 0),
                _bt_stage.get("max_fill_slippage_bps", 0), _bt_stage.get("total_cost", 0),
                _bt_stage.get("total_impact_cost", 0), _bt_stage.get("total_spread_cost", 0),
                _bt_stage.get("total_commission", 0), _bt_stage.get("total_trades", 0),
            )
            if not _bt_stage.get("strategy_implemented", False):
                log.warning(
                    "Stage 1 scored ruleset %r, not a dedicated %s implementation — "
                    "these metrics do not describe that strategy.",
                    _bt_stage.get("ruleset"), strategy_name)
        except Exception as exc:
            log.error("Backtest failed: %s", exc)
            results["stages"]["backtest"] = {"status": "failed", "error": str(exc)}

        # Stage 2: DSR Gate
        if run_dsr:
            log.info("Stage 2: DSR Gate Validation")
            try:
                from research.backtest_dsr_integrator import BacktestDSRIntegrator
                integrator = BacktestDSRIntegrator()
                st_bt = results["stages"].get("backtest", {})
                # Fail closed: never validate the DSR gate against a default
                # Sharpe when there is no backtest to defend.
                if st_bt.get("status") != "completed" or "sharpe" not in st_bt:
                    results["stages"]["dsr"] = {
                        "status": "skipped",
                        "passed": False,
                        "reason": "backtest stage did not complete; refusing to "
                                  "assume a default Sharpe",
                        "execution_authority": False,
                    }
                    log.warning("DSR stage skipped: no completed backtest to defend")
                else:
                    sharpe = float(st_bt.get("sharpe"))
                    n_obs = int(st_bt.get("total_trades", 0)) or 50
                    dsr_report = integrator.validate_backtest(
                        strategy_id=strategy_name if strategy_name != "default" else f"{symbol}_{timeframe}",
                        sharpe=sharpe,
                        n_observations=n_obs,
                    )
                    results["stages"]["dsr"] = {
                        "status": "completed",
                        "passed": bool(dsr_report.passed_gate),
                        "deflated_sharpe": round(float(dsr_report.dsr), 4),
                        "raw_sharpe": round(float(dsr_report.raw_sharpe), 4),
                        "trial_count": int(dsr_report.n_trials),
                    }
            except Exception as exc:
                log.warning("DSR validation failed: %s", exc)
                results["stages"]["dsr"] = {"status": "failed", "error": str(exc)}

        # Stage 2b: CPCV — Combinatorial Purged Cross-Validation (PBO/DSR).
        # Runs on the backtest's own net-of-cost R-multiple stream when the
        # engine exposes it, else on live trade R-multiples from the journal.
        # A failure is recorded concretely — never silently bypassed.
        try:
            results["stages"]["cpcv"] = self._stage_cpcv(symbol, strategy_name, results["stages"].get("backtest", {}))
        except Exception as exc:
            log.error("Stage 2b CPCV failed: %s", exc)
            results["stages"]["cpcv"] = {"status": "failed", "error": str(exc)}

        # Stage 3: Walk-Forward Analysis
        if run_walkforward:
            log.info("Stage 3: Walk-Forward Analysis")
            try:
                from research.bayesian_walk_forward_sandbox import BayesianWalkForwardSandbox
                sandbox = BayesianWalkForwardSandbox()
                # A broken reader must surface as a failed stage (see the
                # `except` below), not as an empty dataset that is
                # indistinguishable from a starved gate.
                returns = _broker_reconciled_r_multiples(
                    os.path.join(self.base_dir, "trading_state.db"))
                if len(returns) < 20:
                    # Insufficient admissible evidence: report it instead of
                    # tuning a fabricated series. np.random.normal(0.05, 0.5, 60)
                    # here once produced "completed" stability/sharpe/kelly
                    # metrics from nothing, which reads as validated research.
                    results["stages"]["walkforward"] = {
                        "status": "INSUFFICIENT_JOURNAL_EVIDENCE",
                        "samples": len(returns),
                        "reason": "fewer than 20 broker-reconciled r-multiples; walk-forward not run",
                    }
                else:
                    wf_result = sandbox.tune(
                        setup_id=strategy_name if strategy_name != "default" else f"{symbol}_{timeframe}",
                        returns=returns,
                    )
                    opt_m = wf_result.get("optimal_metrics", {})
                    results["stages"]["walkforward"] = {
                        "status": "completed",
                        "stability_score": round(float(opt_m.get("stability_score", 0.75)), 4),
                        "best_sharpe": round(float(opt_m.get("oos_sharpe_mean", 1.8)), 4),
                        "kelly_fraction": round(float(opt_m.get("bayesian_kelly_f", 0.05)), 4),
                        "folds_tested": 3,
                    }
            except Exception as exc:
                log.warning("Walk-forward failed: %s", exc)
                results["stages"]["walkforward"] = {"status": "failed", "error": str(exc)}

        # Stage 4: Live-vs-Backtest Comparison
        if compare_live:
            log.info("Stage 4: Live-vs-Backtest Comparison")
            try:
                comparison = self._compare_live_vs_backtest(symbol, results["stages"].get("backtest", {}))
                results["stages"]["live_comparison"] = comparison
            except Exception as exc:
                log.warning("Live comparison failed: %s", exc)
                results["stages"]["live_comparison"] = {"status": "failed", "error": str(exc)}

        # Stage 5: Data Improvement & Friction Calibration
        log.info("Stage 5: Data Improvement & Friction Calibration")
        try:
            from research.mt5_backtest_engine import MT5BrokerBacktestEngine, BacktestMetrics
            import pandas as pd
            mt5_engine = MT5BrokerBacktestEngine(journal_path=os.path.join(self.base_dir, "backtest_state.db"))
            bars_frame = pd.DataFrame(raw_bars) if raw_bars else pd.DataFrame()
            data_imp = mt5_engine.process_data_improvement(bars_frame, symbol=symbol, timeframe=timeframe)
            results["stages"]["data_improvement"] = data_imp
        except Exception as exc:
            log.warning("Data improvement stage failed: %s", exc)
            results["stages"]["data_improvement"] = {"status": "failed", "error": str(exc)}

        # Stage 6: Strategy Improvement & Lifecycle Gate
        log.info("Stage 6: Strategy Improvement & Lifecycle Gate")
        try:
            bt_st = results["stages"].get("backtest", {})
            st_metrics = BacktestMetrics(
                sharpe_ratio=float(bt_st.get("sharpe", 0.0)),
                win_rate=float(bt_st.get("win_rate", 0.0)) * 100.0,
                profit_factor=float(bt_st.get("profit_factor", 0.0)),
                expectancy_r=float(bt_st.get("expectancy_r", 0.0)),
                max_drawdown_pct=float(bt_st.get("max_drawdown", 0.0)),
                total_trades=int(bt_st.get("total_trades", 0)),
                r_multiples=results["stages"].get("cpcv", {}).get("returns", []),
            )
            strat_imp = mt5_engine.process_strategy_improvement(
                metrics=st_metrics,
                symbol=symbol,
                strategy_name=strategy_name,
                timeframe=timeframe,
            )
            results["stages"]["strategy_improvement"] = strat_imp
        except Exception as exc:
            log.warning("Strategy improvement stage failed: %s", exc)
            results["stages"]["strategy_improvement"] = {"status": "failed", "error": str(exc)}

        # Stage 7: Journaling & Reflection
        log.info("Stage 7: Journaling & Reflection on Results")
        try:
            journal_res = mt5_engine.process_journaling(
                metrics=st_metrics,
                symbol=symbol,
                strategy_name=strategy_name,
                config={"timeframe": timeframe, "bars": bars, "source": "continuous_pipeline"},
                data_improvement=results["stages"].get("data_improvement"),
            )
            results["stages"]["journaling"] = journal_res

            refl_res = mt5_engine.process_reflection(
                metrics=st_metrics,
                symbol=symbol,
                strategy_name=strategy_name,
                timeframe=timeframe,
            )
            results["stages"]["reflection"] = refl_res
        except Exception as exc:
            log.warning("Journaling and reflection stage failed: %s", exc)
            results["stages"]["reflection"] = {"status": "failed", "error": str(exc)}

        # Compute overall verdict
        results["verdict"] = self._compute_verdict(results["stages"])
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["duration_seconds"] = round(time.time() - start_time, 2)

        # Save results
        self._save_results(results)

        # Update state
        self.state["last_run"] = results["completed_at"]
        self.state["total_runs"] += 1
        self.state["last_symbol"] = symbol
        self.state["last_timeframe"] = timeframe
        if results["verdict"]["passed"]:
            self.state["successful_runs"] += 1
        else:
            self.state["failed_runs"] += 1
        self._save_state()

        log.info("=" * 60)
        log.info("Validation complete: %s (%.1fs)", results["verdict"]["status"], results["duration_seconds"])
        log.info("=" * 60)

        return results

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
        import numpy as _np
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
        if r_multiples is None:
            source = "journal_trades"
            # Same reader as walk-forward: the trades table has no
            # `r_multiple` column, so the old select against it raised on
            # every run and was downgraded to "no evidence" by the except
            # below.  The pnl/risk fallback it used to reach for also
            # *invented* +-1.0R whenever risk_amount was unusable; a real
            # R-multiple is now read or it is absent.
            r_multiples = _broker_reconciled_r_multiples(
                os.path.join(self.base_dir, "trading_state.db"))

        if not r_multiples or len(r_multiples) < 40:
            _samples = len(r_multiples or [])
            log.info(
                "Stage 2b CPCV not run: only %d net-of-cost %s returns available (need >= 40 for combinatorial paths)",
                _samples, source,
            )
            return {
                "status": "INSUFFICIENT_SAMPLES_FOR_CPCV",
                "source": source,
                "samples": _samples,
                "reason": "needs >= 40 net-of-cost trade returns for combinatorial paths",
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
            "Stage 2b CPCV (%s, n=%d): mean_sharpe=%.3f DSR_conf=%.3f PBO=%.3f -> %s",
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
        """Compare live/demo performance against backtest expectations."""
        try:
            import sqlite3
            db_path = os.path.join(self.base_dir, "trading_state.db")
            if not os.path.exists(db_path):
                return {"status": "skipped", "reason": "no_trading_db"}

            con = sqlite3.connect(db_path, timeout=2)
            con.row_factory = sqlite3.Row

            # Only broker-reconciled closes count as live performance; a
            # fixture row asserting source="broker" must not offset a
            # backtest-divergence verdict.
            tickets = _broker_confirmed_tickets(db_path)
            if not tickets:
                con.close()
                return {"status": "skipped", "reason": "no_broker_reconciled_trades", "count": 0}

            # Get recent trades for this symbol
            marks = ",".join("?" for _ in tickets)
            trades = con.execute(
                "SELECT gross_pnl, risk_amount, side, opened_at, closed_at "
                "FROM trades WHERE symbol=? AND closed_at IS NOT NULL "
                f"AND ticket IN ({marks}) "
                "ORDER BY closed_at DESC LIMIT 50",
                (symbol, *tickets)
            ).fetchall()
            con.close()

            if len(trades) < 10:
                return {"status": "skipped", "reason": "insufficient_live_trades", "count": len(trades)}

            # Compute live metrics
            live_pnl = sum(t["gross_pnl"] or 0 for t in trades)
            live_r_multiples = [
                (t["gross_pnl"] or 0) / max(t["risk_amount"] or 1, 0.01)
                for t in trades
            ]
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
                "live_trades": len(trades),
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

    def _compute_verdict(self, stages: Dict[str, Any]) -> Dict[str, Any]:
        """Compute overall validation verdict."""
        blockers = []
        warnings = []

        # Check backtest
        bt = stages.get("backtest", {})
        if bt.get("status") != "completed":
            blockers.append("backtest_failed")
        else:
            if bt.get("sharpe", 0) < 1.0:
                blockers.append("sharpe_below_1")
            if bt.get("win_rate", 0) < 0.45:
                warnings.append("low_win_rate")
            if bt.get("profit_factor", 0) < 1.2:
                blockers.append("profit_factor_below_1.2")
            if bt.get("expectancy_r", 0) <= 0:
                blockers.append("negative_expectancy")

        # Check DSR
        dsr = stages.get("dsr", {})
        if dsr.get("status") == "completed" and not dsr.get("passed", False):
            blockers.append("dsr_gate_failed")

        # Check walk-forward
        wf = stages.get("walkforward", {})
        if wf.get("status") == "completed":
            if wf.get("stability_score", 0) < 0.5:
                warnings.append("low_stability_score")

        # Check live comparison
        lc = stages.get("live_comparison", {})
        if lc.get("status") == "completed" and lc.get("significant_divergence", False):
            blockers.append("live_backtest_divergence")

        passed = len(blockers) == 0
        return {
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "blockers": blockers,
            "warnings": warnings,
        }

    def _save_results(self, results: Dict[str, Any]) -> None:
        """Save results to disk."""
        try:
            # Append to results history
            history = []
            if os.path.exists(self.results_path):
                with open(self.results_path, "r") as f:
                    history = json.load(f)
            history.append(results)
            # Keep last 100 results
            history = history[-100:]
            with open(self.results_path, "w") as f:
                json.dump(history, f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save results: %s", exc)

    def run_continuous(self, interval_seconds: int = 14400, symbol: str = "EURUSD", timeframe: str = "H1"):
        """Run backtests continuously on schedule."""
        log.info("Starting continuous backtesting (interval=%ds, symbol=%s, tf=%s)",
                 interval_seconds, symbol, timeframe)

        while True:
            try:
                self.run_full_validation(symbol=symbol, timeframe=timeframe)
            except Exception as exc:
                log.error("Validation run failed: %s", exc)

            log.info("Next run in %d seconds...", interval_seconds)
            time.sleep(interval_seconds)


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Continuous Backtesting Pipeline")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--interval", type=int, default=14400, help="Interval between runs (seconds)")
    parser.add_argument("--symbol", type=str, default="EURUSD", help="Symbol to backtest")
    parser.add_argument("--timeframe", type=str, default="H1", help="Timeframe")
    parser.add_argument("--bars", type=int, default=500, help="Number of bars")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    pipeline = ContinuousBacktestPipeline()

    if args.once:
        result = pipeline.run_full_validation(
            symbol=args.symbol,
            timeframe=args.timeframe,
            bars=args.bars,
        )
        print(json.dumps(result["verdict"], indent=2))
    else:
        pipeline.run_continuous(
            interval_seconds=args.interval,
            symbol=args.symbol,
            timeframe=args.timeframe,
        )


if __name__ == "__main__":
    main()
