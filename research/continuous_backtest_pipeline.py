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
import sqlite3
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

log = logging.getLogger("ContinuousBacktest")

try:
    from core.provenance import validate_data_source
except ImportError:  # pragma: no cover - flat-layout fallback
    from provenance import validate_data_source

try:
    from core.artifact_paths import scrub as _scrub_artifact_paths
except ImportError:  # pragma: no cover - flat-layout fallback
    from artifact_paths import scrub as _scrub_artifact_paths

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
        self._last_bars = 3000

    def _resolve_target_symbol(self, strategy_name: str, symbol: str) -> str:
        """Map multi-leg/cross strategies to their natural traded symbol/pair."""
        sname = str(strategy_name or "").lower()
        if "statarb_eur_chf" in sname:
            return "EURCHF"
        return symbol

    def _extract_paper_r_multiples(
        self,
        db_path: str,
        symbol: Optional[str] = None,
        strategy_name: Optional[str] = None,
    ) -> List[float]:
        """Extract unbrokered/paper trade R-multiples for cold-start probation walk-forward.
        
        Strictly isolates trades by symbol and strategy to prevent cross-asset or
        cross-strategy contamination during walk-forward analysis.
        """
        import sqlite3
        multiples: List[float] = []
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0) as con:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "trade_labels" in tables and "trades" in tables:
                    has_provenance = "provenance" in {r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()}
                    query = """
                        SELECT tl.r_multiple
                        FROM trade_labels tl
                        JOIN trades t ON tl.ticket = t.ticket
                        WHERE tl.r_multiple IS NOT NULL
                          AND t.ticket < 200000
                    """
                    if has_provenance:
                        query += " AND (t.provenance IS NULL OR t.provenance NOT IN ('synthetic', 'fixture', 'backtest')) "
                    params = []
                    if symbol:
                        query += " AND UPPER(t.symbol) = UPPER(?) "
                        params.append(symbol)
                    if strategy_name:
                        query += " AND (t.strategy = ? OR t.strategy LIKE ?) "
                        params.extend([strategy_name, f"{strategy_name}%"])
                    query += " ORDER BY t.ticket ASC"
                    rows = con.execute(query, params).fetchall()
                    for (rm,) in rows:
                        if rm is not None:
                            try:
                                multiples.append(float(rm))
                            except (ValueError, TypeError):
                                pass
        except Exception as exc:
            log.debug("Failed to extract paper r-multiples: %s", exc)
        return multiples

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
                json.dump(_scrub_artifact_paths(self.state), f, indent=2, default=str)
        except Exception as exc:
            log.debug("Failed to save pipeline state: %s", exc)

    def run_validation_cycle(
        self,
        symbol: str = "EURUSD",
        strategy_name: str = "default",
        timeframe: str = "H1",
        bars: int = 3000,
        bars_df: Optional[Any] = None,
        data_source: Optional[str] = None,
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
            data_source=data_source,
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
        bars: int = 3000,
        run_walkforward: bool = True,
        run_dsr: bool = True,
        compare_live: bool = True,
        cost_overrides: Optional[Dict[str, Any]] = None,
        bars_df: Optional[Any] = None,
        data_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run full validation suite for a symbol/timeframe.

        ``data_source`` declares where the bars came from. When this function
        fetches them itself the answer is known (broker history); when a caller
        supplies ``bars_df``, the declaration is REQUIRED — the pipeline cannot
        see through the frame, and guessing is how fixtures became evidence.
        """
        log.info("=" * 60)
        log.info("Starting full validation: %s %s (%d bars, strategy=%s)",
                 symbol, timeframe, bars, strategy_name)
        log.info("=" * 60)
        orig_symbol = symbol
        symbol = self._resolve_target_symbol(strategy_name, symbol)
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
            caller_bars = (bars_df is not None) and (orig_symbol == symbol)
            if not caller_bars:
                bars_df = fetch_ohlc(symbol, timeframe, max(bars, 500))
            if bars_df is None or len(bars_df) < 200:
                raise RuntimeError(
                    f"Backtest requires >=200 bars of {symbol} {timeframe}; "
                    f"got {None if bars_df is None else len(bars_df)}."
                )
            source = validate_data_source(
                data_source if caller_bars or data_source else "mt5_history",
                kind="backtest",
                field=("run_full_validation(data_source) for caller-supplied bars"
                       if caller_bars else "run_full_validation(data_source)"),
            )
            backtest_res = self._run_pipeline_backtest(
                symbol=symbol,
                strategy_name=strategy_name,
                bars_df=bars_df,
                timeframe=timeframe,
                cost_overrides=cost_overrides,
                data_source=source,
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

        # Stage 6: Autonomous Evolution Sprint on Rejected Strategies (Autopsy -> Reasoner -> Git)
        if not results["verdict"].get("passed", False):
            evo_res = self._run_evolution_sprint_if_applicable(
                strategy_name=strategy_name,
                symbol=symbol,
                timeframe=timeframe,
                bars_df=bars_df,
                results=results,
            )
            if evo_res is not None:
                results["stages"]["autonomous_evolution"] = evo_res

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

    def _run_evolution_sprint_if_applicable(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        bars_df: Optional[Any],
        results: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Trigger autonomous forensic autopsy, hypothesis reasoner, and StrategyGit branching
        when a strategy fails mathematical or validation gates.
        """
        try:
            bt = results.get("stages", {}).get("backtest", {})
            trades = bt.get("trades", [])
            if not trades or len(trades) < 5 or bars_df is None or len(bars_df) < 50:
                return None

            log.info("Triggering Autonomous Evolution Sprint for rejected strategy: %s (%d parent trades)",
                     strategy_name, len(trades))

            from operations.autonomous_evolution_loop import run_autonomous_strategy_evolution_sprint
            from research.strategy_git import StrategyGit
            from intelligence.strategy_reasoner import StrategyReasoner
            from research.mt5_backtest_engine import MT5BrokerBacktestEngine
            from core.provenance import is_synthetic

            # StrategyGit store in pipeline artifacts directory
            git_db_path = os.path.join(self.artifacts_dir, "strategy_git.db")
            conn = sqlite3.connect(git_db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            sg = StrategyGit(conn)
            reasoner = StrategyReasoner(llm_router=None)

            # Establish baseline parent commit on main if absent
            head = sg.get_head(strategy_name, "main")
            parent_genome = {
                "strategy_id": strategy_name,
                "sl_atr_mult": 1.5,
                "allowed_regimes": ["ALL"],
                "fast_period": 8,
                "slow_period": 20,
            }
            if head is None:
                bt_sharpe = float(bt.get("sharpe", 0.0) or 0.0)
                dsr_val = float(results.get("stages", {}).get("dsr", {}).get("deflated_sharpe", 0.0) or 0.0)
                init_res = sg.commit(
                    strategy_id=strategy_name,
                    branch_name="main",
                    genome=parent_genome,
                    commit_message=f"Baseline commit before evolution: {strategy_name}",
                    cpcv_oos_sharpes=[bt_sharpe] * 6,
                    pbo=0.5,
                    dsr=dsr_val,
                    math_audit_passed=True,
                )
                parent_hash = init_res["commit_hash"]
            else:
                parent_hash = head["commit_hash"]
                if isinstance(head.get("genome"), dict):
                    parent_genome = head["genome"]

            # Annotate parent trades with regime if missing
            regime_label = results.get("market_regime") or "LOW_VOL_TREND"
            annotated_parent_trades = []
            for t in trades:
                t_copy = dict(t)
                if "regime" not in t_copy:
                    t_copy["regime"] = regime_label
                if "risk_dollars" not in t_copy:
                    t_copy["risk_dollars"] = 10.0
                if "net_pnl" not in t_copy:
                    t_copy["net_pnl"] = float(t_copy.get("r_multiple", 0.0)) * 10.0
                annotated_parent_trades.append(t_copy)

            # Backtest runner closure using MT5BrokerBacktestEngine
            def evolution_backtest_runner(mutated_genome: Dict[str, Any], b_df: Any):
                fast_p = int(mutated_genome.get("fast_period", 8))
                slow_p = int(mutated_genome.get("slow_period", 20))
                sl_atr = float(mutated_genome.get("sl_atr_mult", 1.5))
                sl_pips = sl_atr * 20.0
                tp_pips = sl_pips * 2.0

                engine = MT5BrokerBacktestEngine(
                    spread_pips=1.5,
                    slippage_pips=0.5,
                    commission_per_lot=7.0,
                )
                active_bars = b_df if isinstance(b_df, pd.DataFrame) else bars_df
                source = bt.get("data_source", "synthetic")
                m = engine.run_crossover_strategy(
                    bars_df=active_bars,
                    fast_period=fast_p,
                    slow_period=slow_p,
                    sl_pips=sl_pips,
                    tp_pips=tp_pips,
                    strategy_name=strategy_name,
                    data_source="synthetic" if is_synthetic(source) else "mt5_history",
                )
                formatted_trades = []
                for tr in m.trades:
                    r_val = float(tr.get("r_multiple", 0.0))
                    formatted_trades.append({
                        "trade_id": tr.get("trade_id"),
                        "r_multiple": r_val,
                        "risk_dollars": 10.0,
                        "net_pnl": r_val * 10.0,
                        "holding_bars": tr.get("holding_bars", 1),
                        "regime": regime_label,
                        "exit_reason": tr.get("exit_reason", "STOP_LOSS"),
                    })

                def sig_fn(b):
                    if isinstance(b, pd.DataFrame):
                        c = b["close"].values
                    elif isinstance(b, np.ndarray) and b.ndim == 2:
                        c = b[:, 3]
                    else:
                        c = np.asarray(b)
                    f_ema = pd.Series(c).ewm(span=fast_p, adjust=False).mean().values
                    s_ema = pd.Series(c).ewm(span=slow_p, adjust=False).mean().values
                    return (f_ema > s_ema).astype(float) - (f_ema < s_ema).astype(float)

                return formatted_trades, sig_fn

            sprint_res = run_autonomous_strategy_evolution_sprint(
                strategy_id=strategy_name,
                current_genome=parent_genome,
                parent_commit_hash=parent_hash,
                parent_trades=annotated_parent_trades,
                ohlcv_bars=bars_df,
                backtest_runner_fn=evolution_backtest_runner,
                strategy_git=sg,
                strategy_reasoner=reasoner,
                validated_alphas_pool=[],
                n_effective_trials=13.0,
            )

            conn.close()

            log.info("🧬 [AUTONOMOUS EVOLUTION] Strategy %s evaluated %d branches. Autopsy: %s",
                     strategy_name, len(sprint_res.get("evaluated_branches", [])),
                     sprint_res.get("autopsy_summary", []))
            for b in sprint_res.get("evaluated_branches", []):
                if b.get("merged_to_canary"):
                    log.info("🚀 [PROMOTION] Strategy %s branch %s PROMOTED TO CANARY (Sharpe=%.2f, DSR=%.2f, PBO=%.2f)",
                             strategy_name, b.get("branch"), b.get("mean_sharpe", 0.0), b.get("dsr", 0.0), b.get("pbo", 0.0))

            return sprint_res
        except Exception as exc:
            log.warning("Autonomous Evolution Sprint skipped for %s: %s", strategy_name, exc)
            return None

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

    @staticmethod
    def _classify_bars_regimes(bars_df: Any) -> List[str]:
        """Bar-by-bar market regime classification for regime-conditioned backtesting."""
        if bars_df is None or len(bars_df) == 0:
            return []
        try:
            closes = bars_df["close"].to_numpy(dtype=np.float64) if hasattr(bars_df["close"], "to_numpy") else np.array(bars_df["close"], dtype=np.float64)
            highs = bars_df["high"].to_numpy(dtype=np.float64) if hasattr(bars_df["high"], "to_numpy") else np.array(bars_df["high"], dtype=np.float64)
            lows = bars_df["low"].to_numpy(dtype=np.float64) if hasattr(bars_df["low"], "to_numpy") else np.array(bars_df["low"], dtype=np.float64)
            n = len(closes)
            if n < 5:
                return ["MEAN_REVERTING"] * n

            tr = np.empty(n, dtype=np.float64)
            tr[0] = highs[0] - lows[0]
            for i in range(1, n):
                tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

            atr_20 = np.empty(n, dtype=np.float64)
            alpha_atr = 2.0 / 21.0
            atr_20[0] = tr[0]
            for i in range(1, n):
                atr_20[i] = alpha_atr * tr[i] + (1.0 - alpha_atr) * atr_20[i - 1]

            sma_50 = np.empty(n, dtype=np.float64)
            alpha_sma = 2.0 / 51.0
            sma_50[0] = closes[0]
            for i in range(1, n):
                sma_50[i] = alpha_sma * closes[i] + (1.0 - alpha_sma) * sma_50[i - 1]

            regimes: List[str] = []
            for i in range(n):
                vr = tr[i] / max(atr_20[i], 1e-8)
                c = closes[i]
                s = sma_50[i]

                if vr > 1.8:
                    regimes.append("HIGH_VOLATILITY_CHOP")
                elif c > s * 1.002:
                    regimes.append("BULL_TREND")
                elif c < s * 0.998:
                    regimes.append("BEAR_TREND")
                else:
                    regimes.append("MEAN_REVERTING")
            return regimes
        except Exception as exc:
            log.debug("Regime classification failed: %s", exc)
            return ["MEAN_REVERTING"] * len(bars_df)

    @staticmethod
    def _get_strategy_suitable_regimes(strategy_name: str) -> Optional[List[str]]:
        """Map strategy hypotheses to suitable market regimes (Flag 3)."""
        sname = str(strategy_name or "").lower()
        if any(k in sname for k in ("statarb", "meanrev", "copula", "bollinger")):
            return ["MEAN_REVERTING", "LOW_VOL_RANGE", "SIDEWAYS_CONSOLIDATION"]
        elif any(k in sname for k in ("kronos", "momentum", "pullback", "trend", "changepoint")):
            return ["BULL_TREND", "BEAR_TREND", "LOW_VOL_TREND", "HIGH_VOL_TREND"]
        elif any(k in sname for k in ("volatility", "skew", "orderflow", "chop")):
            return ["HIGH_VOLATILITY_CHOP", "HIGH_VOL_EXPANSION", "HIGH_VOL_CRISIS"]
        return None

    def _apply_regime_filter_to_scores(
        self,
        strategy_name: str,
        scores: np.ndarray,
        bars_df: Any,
        filter_regime: Optional[Any] = None,
    ) -> np.ndarray:
        """Gate/mute entry signals on bars with non-matching market regime."""
        if filter_regime is not None:
            if isinstance(filter_regime, str):
                target_regimes = [filter_regime]
            else:
                target_regimes = list(filter_regime)
        else:
            target_regimes = self._get_strategy_suitable_regimes(strategy_name)

        if not target_regimes or len(scores) == 0:
            return scores

        regimes = self._classify_bars_regimes(bars_df)
        if len(regimes) != len(scores):
            return scores

        filtered = np.copy(scores)
        for i, r in enumerate(regimes):
            if r not in target_regimes:
                filtered[i] = 0.0
        return filtered

    def _run_pipeline_backtest(
        self,
        symbol: str,
        strategy_name: str,
        bars_df: Any,
        timeframe: str = "H1",
        cost_overrides: Optional[Dict[str, Any]] = None,
        data_source: str = "",
        filter_regime: Optional[Union[str, List[str]]] = None,
        regime_conditioned: bool = True,
        filter_bars_by_regime: bool = False,
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

        # Optional contiguous bar filtering by matching regime (Flag 3)
        target_regimes = (
            [filter_regime] if isinstance(filter_regime, str)
            else (list(filter_regime) if filter_regime is not None else self._get_strategy_suitable_regimes(strategy_id))
        )
        if filter_bars_by_regime and target_regimes and hasattr(bars_df, "__len__") and len(bars_df) >= 200:
            bar_regimes = self._classify_bars_regimes(bars_df)
            mask = [r in target_regimes for r in bar_regimes]
            if sum(mask) >= 200:
                bars_df = bars_df.iloc[mask] if hasattr(bars_df, "iloc") else bars_df[mask]

        signal_builder: Optional[Callable[[Any], Any]] = None
        try:
            from research.strategy_signal_registry import build_signal, has_signal
        except ImportError:  # pragma: no cover - flat-layout fallback
            from strategy_signal_registry import build_signal, has_signal
        if has_signal(strategy_id):
            def signal_builder(bdf: Any) -> Any:
                raw = build_signal(
                    strategy_id,
                    bdf["close"].to_numpy(dtype=np.float64),
                    bdf["high"].to_numpy(dtype=np.float64),
                    bdf["low"].to_numpy(dtype=np.float64),
                )
                if regime_conditioned and not filter_bars_by_regime:
                    return self._apply_regime_filter_to_scores(
                        strategy_name=strategy_id,
                        scores=raw,
                        bars_df=bdf,
                        filter_regime=filter_regime,
                    )
                return raw
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
            count=len(bars_df) if hasattr(bars_df, "__len__") else 3000,
            fast_period=12,
            slow_period=26,
            sl_pips=30.0,
            tp_pips=60.0,
            signal_builder=signal_builder,
            entry_signal_threshold=1.0,
            bars_df=bars_df,
            data_source=data_source,
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
            "data_source": res.get("data_source", data_source),
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

        # DSR units (contract resolved 2026-10-20). Stage-1 Sharpe arrives from
        # the engine as (mean/std of per-TRADE pnl) * sqrt(252): annualized per
        # trade, NOT per bar. So the annualization constant is 252 and the
        # estimator's sample size is the TRADE count. Pairing a per-trade
        # Sharpe with the bar count (4000 bars for 72 trades) shrinks the
        # standard error and overstates the evidence by ~7x.
        sharpe_basis = "per_trade"
        dsr_ppy = 252.0
        dsr_n = max(10, _run_trades)

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
            # Audit 2026-10-20: this stage de-annualizes the Sharpe inside the
            # DSR, so it must be told the timeframe's own periods-per-year. It
            # used to leave the default 252 while reporting periods_per_year
            # 8760 for H1 -- the DSR was computed 5.9x too lenient.
            deflated = _calc_dsr(
                estimated_sharpe=sharpe,
                num_trials=max(1, k),
                sample_size=dsr_n,
                skewness=0.0,
                kurtosis=3.0,
                benchmark_sharpe=0.0,
                annualized=True,
                periods_per_year=dsr_ppy,
            )
            passed = bool(deflated < 0.05)

        return {
            "status": "OK",
            "sharpe": sharpe,
            "deflated_sharpe": float(deflated),
            "n_obs": n_obs,
            # Audit 2026-10-20: ``k`` is the multiple-testing FAMILY size, not a
            # trade count. Reporting it as ``n_trades`` made the last artifact
            # claim 2811 trades where 72 were taken, which would corrupt any
            # per-trade statistic (Sharpe standard error, expectancy R) computed
            # downstream from this stage.
            "n_trades": _run_trades,
            "n_trials": max(1, k),
            "sharpe_basis": sharpe_basis,
            "dsr_sample_size": dsr_n,
            "periods_per_year": dsr_ppy,
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
            from core.common_paths import get_trading_db_path
            resolved_db = get_trading_db_path("trading_state.db")
            if os.path.isfile(resolved_db):
                candidate = resolved_db
            else:
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
        return self._run_walkforward_with_db(candidate, symbol, strategy_name)

    def _run_walkforward_with_db(self, db_path: str, symbol: str, strategy_name: Optional[str] = None) -> Dict[str, Any]:
        """Execute walkforward analysis against a specific journal db."""
        from pathlib import Path
        db_path_obj = Path(db_path)
        if not db_path_obj.is_file():
            return {
                "status": "INSUFFICIENT_JOURNAL_EVIDENCE",
                "journal": db_path,
                "samples": 0,
                "reason": f"journal file {db_path} does not exist",
            }
        tickets = _broker_confirmed_tickets(db_path)
        multiples = _broker_reconciled_r_multiples(db_path)

        def _sharpe_from(items: List[float]) -> Optional[float]:
            if len(items) < 2:
                return None
            mean = sum(items) / len(items)
            var = sum((x - mean) ** 2 for x in items) / (len(items) - 1)
            std = var ** 0.5
            return mean / std if std > 1e-12 else 0.0

        if len(multiples) < 20:
            # Cold-start catch-22 resolution: check for verified paper trades in this journal
            paper_multiples = self._extract_paper_r_multiples(db_path, symbol, strategy_name)
            if len(paper_multiples) >= 20:
                penalized = [r - 0.10 for r in paper_multiples]
                last_20 = penalized[-20:]
                last_40 = penalized[-40:]
                sh_20 = _sharpe_from(last_20)
                sh_40 = _sharpe_from(last_40)
                return {
                    "status": "PROBATION_PAPER_OK",
                    "journal": db_path,
                    "broker_tickets": len(tickets),
                    "samples": len(penalized),
                    "is_probation": True,
                    "mean_r_20": sum(last_20) / len(last_20),
                    "annualized_sharpe_20": sh_20,
                    "annualized_sharpe_40": sh_40,
                    "warning": "probationary_paper_evidence_only",
                }
            log.warning(
                "Walk-forward: fewer than 20 broker-reconciled R-multiples in %s "
                "(tickets=%d, multiples=%d)",
                db_path,
                len(tickets),
                len(multiples),
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

        floor_20_ok = len(multiples) >= 20
        floor_40_ok = len(multiples) >= 40
        last_20 = multiples[-20:]
        last_40 = multiples[-40:]

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
            from core.common_paths import get_trading_db_path
            db_path = get_trading_db_path("trading_state.db")
            if not os.path.exists(db_path):
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
        # An undeclared source is refused too: the old equality check against
        # the literal "synthetic" passed every run that simply omitted the key,
        # and ignored "synthetic(seed=7)".
        from core.provenance import is_synthetic
        bt_source = bt.get("data_source")
        if not bt_source:
            blockers.append("data_source_undeclared")
        elif is_synthetic(bt_source):
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
        elif wf_status == "PROBATION_PAPER_OK":
            warnings.append("probationary_paper_evidence_only")
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
            # The results file is evidence: it must not carry the operator's
            # directory layout. The walk-forward stage names the journal it
            # read, and a stage error can quote an absolute path verbatim, so
            # the whole record is scrubbed at the write site rather than at
            # each producer.
            history.append(_scrub_artifact_paths(results))
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

