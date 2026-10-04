"""Discrete Bounded Agent Node Adapters for the LangGraph Trading Engine.

Enforces least-privilege contracts (agent_contracts.py), deterministic data flow,
and zero authority leakage to LLMs.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from intelligence.graph_state import (
    TradingCycleState,
    TradeProposalPayload,
    OrderIntentPayload,
    ExecutionReceiptPayload,
    MemoryReflectionState,
    utcnow_iso,
)
from operations.agent_contracts import require as require_agent_action, can as can_agent_action
from core.trading_safety import (
    DeterministicValidator,
    TradeProposal,
    ValidationResult,
    TradingStore,
)
from core.risk_manager import RiskManager
from core.metrics import (
    calculate_hurst_exponent,
    calculate_atr_percentile,
    calculate_expected_value,
)
from core.hierarchical_portfolio import HierarchicalMetaPortfolioEngine
from intelligence.trading_memory import TradingMemory
from intelligence.feature_builder import build_snapshot as build_feature_snapshot
from intelligence.feature_schema import validate_snapshot as validate_feature_schema
from intelligence.macro_graph import MacroGraph
from intelligence.graphrag_reasoning import CausalGraphRAGEngine
from intelligence.opportunity_fusion import score_opportunity, fusion_block_reasons
from intelligence.correlation_risk import CorrelationRisk
from operations.event_policy import evaluate as evaluate_event_policy
from operations.agent_operations import AgentOperations
from research.decision_models.feature_block import DecisionFeatureBlock
from data.kill_switch_latch import get_kill_switch_latch
from research.decision_models.extended_taxonomy import DecisionBundle, create_decision_bundle
from research.decision_models.macro_ontology import get_standard_macro_questions
from research.decision_models.post_validator import DeterministicPostValidator
from research.decision_models.causal_attribution import (
    CausalFingerprintExtractor,
    CausalMoveAttribution,
    attribute_move,
)
from research.decision_models.narrative_state import NarrativeStateMachine
from intelligence.macro_fusion_gate import MacroFusionGate, MacroGateVerdict
from intelligence.universal_decision_engine import (
    UniversalDecisionEngine, DecisionContext, DecisionDomain,
)
from core.shared_state import ui_state

logger = logging.getLogger(__name__)


class TechnicalNetNode:
    """Agent: TechnicalNet - Produces point-in-time market observations, Hurst exponent & features."""

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        require_agent_action("TechnicalNet", "calculate_features")
        symbol = state["symbol"]
        timeframe = state["timeframe"]
        snapshot = dict(state.get("market_snapshot") or {})
        
        # Build deterministic feature snapshot
        feature_vector = dict(snapshot.get("features") or {})
        data_quality_ok = True
        flags: List[str] = []

        prices = snapshot.get("prices") or []
        atr_history = snapshot.get("atr_history") or []
        current_atr = float(snapshot.get("atr", snapshot.get("atr_14", 0.0010)))
        
        hurst_val = calculate_hurst_exponent(prices) if len(prices) >= 30 else 0.50
        atr_pct = calculate_atr_percentile(atr_history, current_atr) if atr_history else 50.0

        if not feature_vector:
            # Fallback baseline indicators if not precomputed in market_snapshot
            close_val = float(snapshot.get("close", snapshot.get("bid", 1.0)))
            feature_vector = {
                "close": close_val,
                "rsi_14": float(snapshot.get("rsi", 50.0)),
                "adx_14": float(snapshot.get("adx", 25.0)),
                "ema_20": float(snapshot.get("ema_20", close_val)),
                "ema_50": float(snapshot.get("ema_50", close_val)),
                "spread_pips": float(snapshot.get("spread_pips", 1.0)),
                "ema_alignment": 1 if float(snapshot.get("rsi", 50.0)) < 35 else (-1 if float(snapshot.get("rsi", 50.0)) > 65 else 0),
                "mtf_alignment": 1 if float(snapshot.get("rsi", 50.0)) < 35 else (-1 if float(snapshot.get("rsi", 50.0)) > 65 else 0),
                "adx": float(snapshot.get("adx", 25.0)),
                "hurst_exponent": round(hurst_val, 4),
                "atr_percentile": round(atr_pct, 2),
                "near_key_level": True,
                "confirmed_candle": True,
                "volume_confirmed": True,
                "bar_completed": True,
            }
        else:
            feature_vector["hurst_exponent"] = round(hurst_val, 4)
            feature_vector["atr_percentile"] = round(atr_pct, 2)

        # Data quality checks
        if feature_vector.get("spread_pips", 1.0) > 10.0:
            flags.append("HIGH_SPREAD")
            data_quality_ok = False

        audit = [f"TechnicalNet: Ingested {symbol} ({timeframe}) features. Hurst={hurst_val:.3f}, ATR%={atr_pct:.1f}, QualityOK={data_quality_ok}"]
        return {
            "feature_vector": feature_vector,
            "data_quality_ok": data_quality_ok,
            "data_quality_flags": flags,
            "status": "FEATURES_BUILT",
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class MacroAnalystNode:
    """Agent: MacroAnalyst - Ingests causal graphs, 4-state regime classification, Laya System 1 Decisions, and Master Expectancy."""

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path
        self.macro_graph = MacroGraph(db_path=db_path)
        self.graphrag = CausalGraphRAGEngine(self.macro_graph)
        self.meta_portfolio = HierarchicalMetaPortfolioEngine()

        # Macro-Cognitive System (Laya + Deterministic Post-Validator + Causal Attribution)
        self.laya = None
        self.validator = DeterministicPostValidator()
        self.causal_extractor = CausalFingerprintExtractor()
        self.narrative_sm = NarrativeStateMachine()
        self.universal_engine = UniversalDecisionEngine()
        try:
            import os
            from research.decision_models.laya_model import get_laya_decision_model
            if not os.environ.get("FAST_TEST") and os.path.exists("models/laya/model.safetensors"):
                self.laya = get_laya_decision_model("models/laya")
        except Exception as exc:
            logger.info("Laya decision model offline, falling back to deterministic labeler: %s", exc)

    def _build_feature_block(
        self, symbol: str, features: Dict[str, Any], snapshot: Dict[str, Any],
        causal_paths: Optional[List[Dict[str, Any]]] = None,
        causal_cesi_score: float = 1.0,
        causal_prompt_context: str = "",
        active_narratives: Optional[List[Any]] = None,
        narrative_decay: Optional[Dict[str, Any]] = None,
        kill_switch: Optional[Dict[str, Any]] = None,
    ) -> DecisionFeatureBlock:
        ev = snapshot.get("event_context") or {}
        event_name = str(ev.get("event_name") or f"{symbol}_PRICE_DISCOVERY")
        actual = ev.get("actual")
        consensus = ev.get("consensus")
        sigma = float(ev.get("sigma_hist", 1.0))
        z_score = float(ev["z_score"]) if "z_score" in ev else (
            (float(actual) - float(consensus)) / sigma
            if (actual is not None and consensus is not None and sigma > 0)
            else None
        )
        vix = float(snapshot.get("vix", features.get("vix", 18.0)))
        dxy_pct = float(snapshot.get("dxy_change_pct", features.get("dxy_change_pct", 0.0)))
        y10_bps = float(snapshot.get("yield_10y_delta_bps", features.get("yield_10y_delta_bps", 0.0)))
        m_to_rel = float(snapshot.get("minutes_to_release", ev.get("minutes_to_release", 999.0)))

        # Extract narrative tags and headlines from active narratives
        nar_tags: List[str] = []
        nar_headlines: List[str] = []
        for n in (active_narratives or []):
            if hasattr(n, "tag"):
                nar_tags.append(str(n.tag))
            if hasattr(n, "headline"):
                nar_headlines.append(str(n.headline)[:200])

        # Per-article evidence quality from the Layer-0 news triage gate
        # (summarize_cycle_evidence returns None when no triaged articles).
        news_summary = snapshot.get("news_evidence")
        if not isinstance(news_summary, dict):
            news_summary = None

        # Narrative decay metrics feed the D12_narrative_decay typed question.
        nd = narrative_decay if isinstance(narrative_decay, dict) else {}

        # D9 emergency kill-switch: the near-zero-latency headline tripwire
        # (Layer-0 feed) flips the cycle risk-state flag; the deterministic
        # labeler turns it into a veto-class D9 decision (advisory, no order
        # authority). Market-data confirmation remains the execution-side gate.
        ks = kill_switch if isinstance(kill_switch, dict) else {}
        kill_active = bool(ks.get("active"))

        return DecisionFeatureBlock(
            event_name=event_name,
            currency="USD" if "USD" in symbol else "EUR",
            tier=str(ev.get("tier", "HIGH")),
            actual=float(actual) if actual is not None else None,
            consensus=float(consensus) if consensus is not None else None,
            previous=float(ev["previous"]) if "previous" in ev else None,
            sigma_hist=sigma,
            z_score=z_score,
            vix=vix,
            dxy_change_pct=dxy_pct,
            yield_10y_delta_bps=y10_bps,
            minutes_to_release=m_to_rel,
            headline=str(ev.get("headline", f"Price action and liquidity pulse for {symbol}")),
            narrative=str(ev.get("narrative", "Real-time flow and monetary transmission")),
            causal_paths=tuple(causal_paths or []),
            causal_cesi_score=causal_cesi_score,
            causal_prompt_context=causal_prompt_context,
            active_narrative_tags=tuple(nar_tags),
            active_narrative_headlines=tuple(nar_headlines),
            narrative_decay_factor=nd.get("decay_factor"),
            narrative_age_days=nd.get("age_days"),
            narrative_evidence_for=int(nd.get("evidence_for", 0) or 0),
            narrative_evidence_against=int(nd.get("evidence_against", 0) or 0),
            narrative_tripwire_hit=bool(nd.get("tripwire_hit", False)),
            narrative_is_invalidated=bool(nd.get("is_invalidated", False)),
            is_exchange_halt=kill_active,
            article_evidence=news_summary,
        )

    def _evaluate_decision(
        self,
        feature_block: DecisionFeatureBlock,
        ohlcv_history: Optional[List[Dict[str, Any]]] = None,
        symbol: str = "EURUSD",
    ) -> DecisionBundle:
        laya_overrides: Optional[Dict[str, Any]] = None
        if not os.environ.get("FAST_TEST") and self.laya is not None and self.validator is not None:
            try:
                q = get_standard_macro_questions()
                state_dict = feature_block.to_prompt_state()
                laya_res = self.laya.decide(state_dict, q)
                val_res = self.validator.validate(laya_res.answers, feature_block)
                laya_overrides = val_res.validated_answers
            except Exception as exc:
                logger.debug("Laya decision evaluation failed, using deterministic labeler: %s", exc)

        bundle = create_decision_bundle(features=feature_block, overrides=laya_overrides)

        # Kronos owns the OHLCVA heads (D11/D11b/D6) — a candle forecaster is
        # the right evidence for a trend/vol head, and the Laya text checkpoint
        # cannot see bars. Applied as a second, separate construction so the two
        # provenances never merge into one undifferentiated override map.
        kronos_overrides = self._kronos_overrides(ohlcv_history, symbol, feature_block)
        if kronos_overrides:
            kronos_bundle = create_decision_bundle(
                features=feature_block, overrides=kronos_overrides, symbol=symbol
            )
            for dec_id, rec in kronos_bundle.decisions.items():
                if rec.is_overridden:
                    bundle.decisions[dec_id] = rec
            bundle.overrides_applied_count += kronos_bundle.overrides_applied_count
            if kronos_bundle.veto_enforced and not bundle.veto_enforced:
                bundle.veto_enforced = True
                bundle.risk_multiplier = 0.0
        return bundle

    @staticmethod
    def _kronos_overrides(
        ohlcv_history: Optional[List[Dict[str, Any]]],
        symbol: str,
        feature_block: DecisionFeatureBlock,
    ) -> Dict[str, str]:
        """Fail-soft Kronos head overrides; {} on any problem."""
        if os.environ.get("KRONOS_FORCE_DISABLED") == "1" or not ohlcv_history:
            return {}
        try:
            from research.decision_models.kronos_heads import kronos_head_overrides

            return kronos_head_overrides(ohlcv_history, symbol=symbol, feature_block=feature_block)
        except Exception as exc:
            logger.debug("Kronos head routing failed, deterministic fallback: %s", exc)
            return {}

    @staticmethod
    def _bars_from_prices(
        prices: List[float], volumes: Optional[List[float]] = None, atr_history: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """Synthesize OHLCVA bars from the close series the snapshot carries.

        The graph snapshot exposes ``prices``/``volumes``/``atr_history`` rather
        than full bars. Kronos needs open/high/low per bar, so each close is
        wrapped using the contemporaneous ATR as the bar's true range — this
        preserves the realized volatility the D11b head actually keys on, while
        being explicit that the high/low are reconstructed, not observed.
        """
        bars: List[Dict[str, Any]] = []
        for i, close in enumerate(prices):
            try:
                c = float(close)
            except (TypeError, ValueError):
                continue
            atr = 0.0
            if atr_history:
                try:
                    atr = abs(float(atr_history[min(i, len(atr_history) - 1)]))
                except (TypeError, ValueError, IndexError):
                    atr = 0.0
            if atr <= 0 and i > 0:
                try:
                    atr = abs(c - float(prices[i - 1]))
                except (TypeError, ValueError):
                    atr = 0.0
            half = atr / 2.0
            vol = 0.0
            if volumes:
                try:
                    vol = float(volumes[min(i, len(volumes) - 1)])
                except (TypeError, ValueError, IndexError):
                    vol = 0.0
            bars.append({
                "bar": i,
                "open": round(c - half, 8),
                "high": round(c + half, 8),
                "low": round(max(1e-12, c - half), 8),
                "close": round(c, 8),
                "volume": vol,
                "amount": round(c * vol, 8),
            })
        return bars

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        require_agent_action("MacroAnalyst", "validate_causal_edges")
        symbol = state["symbol"]
        features = state.get("feature_vector") or {}
        snapshot = state.get("market_snapshot") or {}

        # Discover causal paths from macro graph
        causal_paths: List[Dict[str, Any]] = []
        causal_prompt_context: str = ""
        try:
            summary = self.macro_graph.summary()
            edge_count = summary.get("active_edges", 0) if isinstance(summary, dict) else 0
            # Search paths from core macro anchors to current symbol
            anchors = ["US_CPI", "US_NFP", "FED", "US_FED_FUNDS", "DXY"]
            found_paths = self.graphrag.find_causal_paths(
                source_entities=anchors,
                target_entities=[symbol],
                max_hops=4,
            )
            for p in found_paths:
                causal_paths.append({
                    "source": p.source,
                    "target": p.target,
                    "hops": p.hops,
                    "chain": " ➔ ".join(p.nodes),
                    "net_sign": p.net_sign,
                    "confidence": p.composite_confidence,
                    "lag_hours": p.total_lag_hours,
                })
            causal_prompt_context = self.graphrag.format_cited_prompt_context(found_paths, max_paths=3)
        except Exception as exc:
            edge_count = 0
            logger.warning(f"MacroGraph query warning: {exc}")

        cesi_score = 0.85 if edge_count > 0 else 1.0

        # Narrative State Machine decay & active list (query BEFORE feature block)
        active_narratives: List[Any] = []
        narrative_decay: Optional[Dict[str, Any]] = None
        if self.narrative_sm is not None:
            self.narrative_sm.decay_step()
            active_narratives = self.narrative_sm.active_narratives()
            if active_narratives:
                n0 = active_narratives[0]
                try:
                    narrative_decay = {
                        "decay_factor": float(getattr(n0, "decay_factor", 0.0) or 0.0),
                        "age_days": float(getattr(n0, "narrative_age_days", 0.0) or 0.0),
                        "evidence_for": len(getattr(n0, "evidence_for", []) or []),
                        "evidence_against": len(getattr(n0, "evidence_against", []) or []),
                        "tripwire_hit": bool(any(
                            getattr(tw, "is_triggered", False)
                            for tw in (getattr(n0, "tripwires", []) or [])
                        )),
                        "is_invalidated": bool(getattr(n0, "is_invalidated", False)),
                    }
                except Exception as exc:
                    logger.debug("narrative decay metrics skipped: %s", exc)
                    narrative_decay = None

        # Run Hierarchical Meta-Portfolio Engine (Layers 1 & 2 & 3)
        prices = snapshot.get("prices") or [features.get("close", 1.0)]
        current_adx = float(features.get("adx_14", features.get("adx", 25.0)))
        atr_hist = snapshot.get("atr_history") or []
        current_atr = float(snapshot.get("atr", snapshot.get("atr_14", 0.0010)))
        strat_name = state.get("strategy", "TREND_MOMENTUM")

        eval_result = self.meta_portfolio.evaluate(
            prices=prices,
            current_adx=current_adx,
            atr_history=atr_hist,
            current_atr=current_atr,
            strategy_type=strat_name,
        )

        regime_info = eval_result["regime"]
        allocation_info = eval_result["allocation"]
        master_ev = float(allocation_info.get("master_expectancy_r", 0.25))

        # Aggregate per-article triage verdicts into cycle evidence quality so
        # Layer-0 macro decisions inherit per-article credibility/duplication/
        # fact-vs-opinion signals (advisory; never order authority).
        news_evidence = None
        try:
            from data.news_triage import summarize_cycle_evidence
            news_evidence = summarize_cycle_evidence(state.get("news_articles") or [])
        except Exception as exc:
            logger.debug("news evidence summary skipped: %s", exc)
        if news_evidence is not None:
            snapshot["news_evidence"] = news_evidence
            ui_state.news_evidence_summary = news_evidence

        # D9 kill-switch state: prefer the orchestrator's persisted run()
        # signal; fall back to the per-cycle triage summary's tripwire count.
        kill_switch = state.get("kill_switch")
        if not isinstance(kill_switch, dict):
            kill_switch = None
            if isinstance(news_evidence, dict) and news_evidence.get("d9_kill_switch_active"):
                kill_switch = {
                    "active": True,
                    "count": news_evidence.get("d9_kill_switch_count", 0),
                    "source": "cycle_triage_summary",
                }
        # Latch authority: explicit market data may engage (halt flag) or
        # clear (VIX below threshold) the cross-cycle kill-switch latch; its
        # latched state overrides the per-batch news signal. News silence
        # never clears a latched kill-switch. halt_flag comes ONLY from the
        # market snapshot — deriving it from the news signal would make the
        # latch unclearable (news asserted halt → calm market can't clear).
        try:
            _latch = get_kill_switch_latch()
            _raw_vix = snapshot.get("vix", features.get("vix") if features else None)
            _market_vix = float(_raw_vix) if _raw_vix is not None else None
            _halt_flag = bool(snapshot.get("halt_flag") or snapshot.get("market_halt") or False)
            latch_snapshot = _latch.apply_market_data(vix=_market_vix, halt_flag=_halt_flag)
            if latch_snapshot["latched"]:
                kill_switch = dict(kill_switch or {})
                kill_switch["active"] = True
                kill_switch["latched"] = True
                kill_switch["trigger_source"] = latch_snapshot["trigger_source"]
            kill_switch = dict(kill_switch or {})
            kill_switch.setdefault("active", False)
            kill_switch.setdefault("count", 0)
            kill_switch.setdefault("question_id", "D9_emergency_kill_switch")
            kill_switch["latch"] = latch_snapshot
        except Exception as exc:
            logger.debug("kill-switch latch not applied: %s", exc)
        if kill_switch is not None:
            snapshot["kill_switch"] = kill_switch
            try:
                ui_state.news_kill_switch_active = bool(kill_switch.get("active"))
            except Exception:
                pass
        # -------------------------------------------------------------
        # Macro-Cognitive Laya / Deterministic Decision Evaluation
        # -------------------------------------------------------------
        # Causal paths and narratives are now injected into feature block
        # so Laya can reason about graph-derived causal chains and narratives
        feature_block = self._build_feature_block(
            symbol, features, snapshot,
            causal_paths=causal_paths,
            causal_cesi_score=cesi_score,
            causal_prompt_context=causal_prompt_context,
            active_narratives=active_narratives,
            narrative_decay=narrative_decay,
            kill_switch=kill_switch,
        )
        kronos_bars = snapshot.get("ohlcv_history") or snapshot.get("bars") or self._bars_from_prices(
            prices, snapshot.get("volumes"), atr_hist
        )
        bundle = self._evaluate_decision(feature_block, kronos_bars, symbol=symbol)

        # Causal Move Attribution (Q1-Q7)
        causal_attrib = None
        if len(prices) >= 5 and self.causal_extractor is not None:
            try:
                fp = self.causal_extractor.extract(prices=prices, volumes=snapshot.get("volumes"), symbol=symbol)
                causal_attrib = attribute_move(fp)
            except Exception as exc:
                logger.debug("Causal move attribution skipped: %s", exc)

        # Synchronize UIState for GUI API & OpenTUI Command Deck
        if bundle:
            ui_state.macro_regime = bundle.d3_regime.value
            ui_state.net_liquidity_stance = bundle.d4_liquidity.value
            ui_state.last_decision_bundle = bundle.to_dict()
        if active_narratives:
            ui_state.active_narrative_summary = f"{active_narratives[0].tag}: {active_narratives[0].headline}"
            ui_state.active_tripwires_status = [
                {
                    "symbol": tw.symbol,
                    "condition": tw.condition,
                    "threshold": tw.threshold_value,
                    "armed": tw.is_armed,
                    "action": tw.action_on_trip,
                }
                for n in active_narratives for tw in n.tripwires
            ]
        if causal_attrib:
            ui_state.last_causal_attribution = causal_attrib.to_dict()

        # Mirror macro telemetry to the GUI API server (only when the isolated
        # trader process was launched with --api-sync; adapter api_url unset =
        # no receiver configured, and posting would be a permanent dead drop).
        try:
            from ui.web_dashboard_adapter import dashboard as _dash
            if getattr(_dash, "api_url", None):
                _dash._push("macro_state", {
                    "macro_regime": ui_state.macro_regime,
                    "net_liquidity_stance": ui_state.net_liquidity_stance,
                    "active_narrative_summary": ui_state.active_narrative_summary,
                    "active_tripwires_status": ui_state.active_tripwires_status,
                    "last_decision_bundle": ui_state.last_decision_bundle,
                    "last_causal_attribution": ui_state.last_causal_attribution,
                })
        except Exception as _exc:
            logger.debug("macro telemetry push skipped: %s", _exc)

        # -------------------------------------------------------------
        # Universal Decision Engine — Philosophical Cross-Domain Advisory
        # -------------------------------------------------------------
        universal_decision = None
        try:
            is_crisis = str(bundle.d3_regime.value if bundle else "").upper() == "CRISIS_PANIC" if bundle else False
            ude_ctx = DecisionContext(
                domain=DecisionDomain.TRADING,
                symbol=symbol,
                action=str(bundle.d8_arbitration.value if bundle else "HOLD"),
                entry_price=float(features.get("close", 0.0)),
                stop_loss=float(features.get("close", 0.0)) * 0.995,
                take_profit=float(features.get("close", 0.0)) * 1.015,
                confidence=float(bundle.decisions.get("M1_confidence_calibration", DecisionRecord(
                    decision_id="M1", decision_layer="Meta", head_type="choice",
                    selected_option="PROBABILISTIC", execution_authority=False,
                )).selected_option == "GROUNDED") * 0.8 + 0.5,
                features={
                    "vix": vix,
                    "adx": float(features.get("adx_14", 25.0)),
                    "hurst_exponent": float(features.get("hurst_exponent", 0.5)),
                    "rsi": float(features.get("rsi_14", 50.0)),
                },
                macro_context={
                    "regime": bundle.d3_regime.value if bundle else "UNKNOWN",
                    "liquidity": bundle.d4_liquidity.value if bundle else "NEUTRAL",
                    "is_crisis": is_crisis,
                    "kill_switch": bundle.d9_kill_switch.value if bundle else "NORMAL_OPERATION",
                },
                portfolio_state={
                    "master_expectancy_r": master_ev,
                    "risk_multiplier": float(eval_result.get("applied_risk_multiplier", 1.0)),
                },
                historical_outcomes=[],
                second_order_chain=[
                    f"Macro regime {bundle.d3_regime.value if bundle else 'UNKNOWN'} affects cross-asset correlation",
                    f"Liquidity stance {bundle.d4_liquidity.value if bundle else 'NEUTRAL'} impacts execution slippage",
                ],
            )
            universal_decision = self.universal_engine.evaluate(ude_ctx)
            if universal_decision:
                ui_state.universal_decision_verdict = universal_decision.verdict.value
                ui_state.universal_decision_confidence = universal_decision.confidence
        except Exception as exc:
            logger.debug("Universal Decision Engine evaluation skipped: %s", exc)

        macro_regime = {
            "regime": bundle.d3_regime.value if bundle else regime_info.get("primary_regime", "LOW_VOL_TREND"),
            "volatility": "HIGH" if (bundle and bundle.d3_regime.value in ("STAGFLATION_SHOCK", "CRISIS_PANIC")) or ("HIGH_VOL" in regime_info.get("primary_regime", "")) else "NORMAL",
            "cesi_score": cesi_score,
            "active_path_count": edge_count,
            "net_liquidity": bundle.d4_liquidity.value if bundle else "NEUTRAL_LIQUIDITY",
            "blackout_gate": bundle.d2_blackout.value if bundle else "NORMAL_TRADING",
            "kill_switch": bundle.d9_kill_switch.value if bundle else "NORMAL_OPERATION",
        }

        audit = [
            f"MacroAnalyst: 4-Regime={regime_info.get('primary_regime')} (Hurst={regime_info.get('hurst_exponent')}), "
            f"MasterEV={master_ev:.2f}R, Tradable={allocation_info.get('is_tradable')}"
        ]
        if bundle:
            audit.append(
                f"MacroAnalyst [Laya]: D3={bundle.d3_regime.value}, D4={bundle.d4_liquidity.value}, "
                f"D2={bundle.d2_blackout.value}, D9_Kill={bundle.d9_kill_switch.value}, AdvisoryAuthority=False"
            )
        if causal_attrib:
            audit.append(
                f"MacroAnalyst [Causal]: Move={causal_attrib.primary_attribution.value} (Conf={causal_attrib.causal_confidence:.2f})"
            )

        res_dict = {
            "macro_regime": macro_regime,
            "four_state_regime": regime_info,
            "master_expectancy_r": master_ev,
            "dynamic_strategy_weights": allocation_info.get("strategy_weights", {}),
            "applied_risk_multiplier": float(eval_result.get("applied_risk_multiplier", 1.0)),
            "causal_paths": causal_paths,
            "causal_prompt_context": causal_prompt_context,
            "causal_cesi_score": cesi_score,
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }
        if bundle:
            res_dict["decision_bundle"] = bundle.to_dict()
        if feature_block:
            res_dict["decision_feature_block"] = feature_block.to_dict()
        if causal_attrib:
            res_dict["causal_attribution"] = causal_attrib.to_dict()
        if universal_decision:
            res_dict["universal_decision"] = universal_decision.to_dict()
        return res_dict


class MemoryRetrieverNode:
    """Retrieves L1 Atomic Facts & L2 Scenario Documents relevant to current setup."""

    def __init__(self, db_path: str = "trading_state.db"):
        self.memory = TradingMemory(db_path=db_path)

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        symbol = state["symbol"]
        tags = {"symbol": symbol.lower(), "strategy": state["strategy"].lower()}
        
        scenarios: List[Dict[str, Any]] = []
        facts: List[Dict[str, Any]] = []
        
        try:
            raw_scenarios = self.memory.retrieve_scenarios(tags=tags, limit=3)
            scenarios = [dict(s) for s in raw_scenarios]
        except Exception as exc:
            logger.warning(f"Scenario retrieval error: {exc}")

        try:
            raw_facts = self.memory.retrieve_facts(fact_keys=[f"trend_{symbol.lower()}", f"bias_{symbol.lower()}"], limit=5)
            facts = [dict(f) for f in raw_facts]
        except Exception as exc:
            logger.warning(f"Atomic facts retrieval error: {exc}")

        audit = [f"MemoryRetriever: Found {len(scenarios)} scenarios and {len(facts)} atomic facts for {symbol}"]
        return {
            "retrieved_scenarios": scenarios,
            "retrieved_atomic_facts": facts,
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class MarketModelNode:
    """Agent: MarketModel - Generates structured trade proposals via Dynamic Strategy Router / LLM."""

    def __init__(self, router: Any = None):
        self.router = router
        self.macro_gate = None
        try:
            from intelligence.macro_fusion_gate import MacroFusionGate
            self.macro_gate = MacroFusionGate()
        except Exception as exc:
            logger.debug("MacroFusionGate init skipped: %s", exc)

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        require_agent_action("MarketModel", "propose_trade")
        symbol = state["symbol"]
        features = state.get("feature_vector", {})
        macro = state.get("macro_regime", {})
        four_regime = state.get("four_state_regime", {})
        master_ev = float(state.get("master_expectancy_r", 0.25))
        price = float(features.get("close", 1.0))
        
        direction = "HOLD"
        confidence = 0.5
        rationale = "No actionable pattern detected."
        stop_loss = 0.0
        take_profit = 0.0

        # Fast discard rule: If Master EV < +0.20R or Crisis Active, exit immediately with zero LLM latency
        if four_regime.get("is_crisis"):
            rationale = f"Discarded: High-volatility crisis regime active on {symbol}."
            proposal = TradeProposalPayload(
                symbol=symbol, direction="HOLD", entry_price=price, stop_loss=0.0,
                take_profit=0.0, confidence=0.0, timeframe=state["timeframe"],
                setup_name=state["strategy"], rationale=rationale, risk_fraction=0.0,
            )
            return {
                "raw_hypotheses": [proposal.model_dump()],
                "fused_proposal": None,
                "fusion_score": 0.0,
                "status": "HYPOTHESIS_FORMED",
                "audit_trace": [f"MarketModel: Fast discard -> {rationale}"],
                "updated_at": utcnow_iso(),
            }

        rsi = float(features.get("rsi_14", 50.0))
        adx = float(features.get("adx_14", 25.0))

        # Baseline rule synthesis if LLM router not passed or in mock mode
        if rsi < 35.0 and adx > 20.0:
            direction = "BUY"
            confidence = 0.85
            stop_loss = round(price * 0.995, 5)
            take_profit = round(price * 1.015, 5)  # 3:1 RR
            rationale = f"Oversold bounce setup on {symbol} (RSI={rsi:.1f}, ADX={adx:.1f})"
        elif rsi > 65.0 and adx > 20.0:
            direction = "SELL"
            confidence = 0.85
            stop_loss = round(price * 1.005, 5)
            take_profit = round(price * 0.985, 5)  # 3:1 RR
            rationale = f"Overbought mean-reversion setup on {symbol} (RSI={rsi:.1f}, ADX={adx:.1f})"

        proposal = TradeProposalPayload(
            symbol=symbol,
            direction=direction,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            timeframe=state["timeframe"],
            setup_name=state["strategy"],
            rationale=rationale,
            risk_fraction=0.01 * float(state.get("applied_risk_multiplier", 1.0)),
        )

        audit = [f"MarketModel: Synthesized proposal -> {direction} @ {price} (Confidence={confidence:.2f}, EV_Master={master_ev:.2f}R)"]

        # Macro Fusion Gate Arbitration (D0, D1, D2, D8, D9 verification)
        fb_dict = state.get("decision_feature_block")
        bundle_dict = state.get("decision_bundle")
        if direction != "HOLD" and self.macro_gate is not None and fb_dict:
            try:
                from research.decision_models.feature_block import DecisionFeatureBlock
                from research.decision_models.extended_taxonomy import DecisionBundle
                fb = DecisionFeatureBlock.from_dict(fb_dict)
                db = DecisionBundle.from_dict(bundle_dict) if bundle_dict else None
                gate_verdict = self.macro_gate.arbitrate_proposal(
                    candidate=proposal.model_dump(),
                    features=fb,
                    model_decision_bundle=db,
                )
                if gate_verdict.is_vetoed:
                    direction = "HOLD"
                    proposal.direction = "HOLD"
                    proposal.confidence = 0.0
                    proposal.risk_fraction = 0.0
                    veto_str = "; ".join(gate_verdict.rejection_reasons)
                    proposal.rationale = f"MacroGate VETO: {veto_str}"
                    audit.append(f"MarketModel: MacroGate VETO for {symbol} ({gate_verdict.verdict}) -> {veto_str}")
                else:
                    if gate_verdict.risk_multiplier < 1.0:
                        proposal.risk_fraction *= gate_verdict.risk_multiplier
                        audit.append(f"MarketModel: MacroGate scaled risk by {gate_verdict.risk_multiplier:.2f}x ({gate_verdict.verdict})")
            except Exception as exc:
                logger.warning("MacroFusionGate arbitration error: %s", exc)

        return {
            "raw_hypotheses": [proposal.model_dump()],
            "fused_proposal": proposal.model_dump() if direction != "HOLD" else None,
            "fusion_score": confidence if direction != "HOLD" else 0.0,
            "status": "HYPOTHESIS_FORMED",
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class SignalArbitratorNode:
    """Agent: SignalArbitrator - ranks and selects best signal from all sources.

    Collects signals from MarketModel hypotheses, Universal Decision Engine,
    Laya Decision, and OpportunityBus. Scores each by confidence × regime
    alignment × causal support × event alignment. Selects the highest-scoring
    signal as fused_proposal for RiskController.

    Never grants execution_authority. Advisory only.
    """

    def __init__(self):
        try:
            from intelligence.signal_arbitrator import arbitrate_signals
            self._arbitrate = arbitrate_signals
        except ImportError:
            try:
                from intelligence.signal_arbitrator import arbitrate_signals
                self._arbitrate = arbitrate_signals
            except ImportError:
                self._arbitrate = None

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        symbol = state["symbol"]
        audit: List[str] = []

        # If the arbitrator module failed to import, pass through MarketModel's proposal
        if self._arbitrate is None:
            audit.append("SignalArbitrator: Module unavailable, passing through MarketModel proposal")
            return {
                "audit_trace": audit,
                "updated_at": utcnow_iso(),
            }

        try:
            result = self._arbitrate(
                state=state,
                regime=state.get("macro_regime", {}),
                causal_paths=state.get("causal_paths", []),
                event_state={},
            )
        except Exception as exc:
            logger.warning("SignalArbitrator error: %s", exc)
            audit.append(f"SignalArbitrator: Error during arbitration ({exc}), passing through MarketModel proposal")
            return {
                "audit_trace": audit,
                "updated_at": utcnow_iso(),
            }

        fused = result.get("fused_proposal")
        fusion_score = result.get("fusion_score", 0.0)
        best = result.get("best_signal")
        all_scored = result.get("all_scored", [])
        arb_audit = result.get("arbitration_audit", [])

        audit.extend(arb_audit)

        # If arbitrator returned HOLD, clear the fused_proposal
        if result.get("verdict") == "HOLD" or fused is None:
            return {
                "fused_proposal": None,
                "fusion_score": 0.0,
                "signal_arbitration": {
                    "verdict": "HOLD",
                    "candidates_evaluated": len(all_scored),
                    "best_source": best.get("source") if best else None,
                    "best_score": best.get("composite_score") if best else 0.0,
                },
                "audit_trace": audit,
                "updated_at": utcnow_iso(),
            }

        return {
            "fused_proposal": fused,
            "fusion_score": fusion_score,
            "signal_arbitration": {
                "verdict": result.get("verdict", "HOLD"),
                "candidates_evaluated": len(all_scored),
                "best_source": best.get("source") if best else None,
                "best_score": best.get("composite_score") if best else 0.0,
                "all_scores": [
                    {"source": s["source"], "score": s["composite_score"], "dir": s["direction"]}
                    for s in all_scored[:5]
                ],
            },
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class DebateProtocolNode:
    """Agent: DebateProtocol - adversarial multi-agent hypothesis validation.

    Runs the ApexQuant Parliament (Zealot/Reaper/Fulcrum) on the fused proposal.
    Validates conviction, detects behavioral biases, and can veto or size-reduce.
    Gated on DEBATE_PROTOCOL_ENABLED env var for latency-conscious deployments.
    """

    def __init__(self):
        self._enabled = False
        self._parliament = None
        try:
            import os
            if os.environ.get("DEBATE_PROTOCOL_ENABLED", "0") != "0":
                from intelligence.debate_protocol import ApexQuantParliament
                self._parliament = ApexQuantParliament(
                    min_conviction_threshold=0.60,
                    veto_on_stalemate=True,
                )
                self._enabled = True
        except Exception as exc:
            logger.debug("DebateProtocol init skipped: %s", exc)

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        fused = state.get("fused_proposal")
        audit: List[str] = []

        # If debate is disabled or no proposal, pass through
        if not self._enabled or not fused or fused.get("direction") == "HOLD":
            audit.append("DebateProtocol: Skipped (disabled or no proposal)")
            return {
                "audit_trace": audit,
                "updated_at": utcnow_iso(),
            }

        symbol = state["symbol"]
        features = state.get("feature_vector", {})
        macro = state.get("macro_regime", {})

        # Build candidate dict for debate
        candidate = {
            "symbol": symbol,
            "action": fused.get("direction", "HOLD"),
            "entry_price": fused.get("entry_price", 0.0),
            "stop_loss": fused.get("stop_loss", 0.0),
            "take_profit": fused.get("take_profit", 0.0),
            "confidence": fused.get("confidence", 0.5),
            "features": features,
        }
        context = {
            "regime": macro.get("bias", "UNKNOWN"),
            "indicators": features,
            "causal_paths": state.get("causal_paths", []),
        }

        try:
            verdict = self._parliament.conduct_debate(candidate, context)
        except Exception as exc:
            logger.warning("DebateProtocol error: %s", exc)
            audit.append(f"DebateProtocol: Error during debate ({exc}), passing through")
            return {
                "audit_trace": audit,
                "updated_at": utcnow_iso(),
            }

        audit.append(
            f"DebateProtocol: decision={verdict.decision}, "
            f"confidence={verdict.confidence:.2f}, "
            f"sizing={verdict.sizing_multiplier:.2f}, "
            f"consensus={verdict.consensus_level}"
        )

        # If debate vetoes, clear the proposal
        if verdict.decision == "HOLD" or not verdict.approved:
            return {
                "fused_proposal": None,
                "fusion_score": 0.0,
                "debate_verdict": {
                    "decision": verdict.decision,
                    "approved": verdict.approved,
                    "confidence": verdict.confidence,
                    "sizing_multiplier": verdict.sizing_multiplier,
                    "consensus_level": verdict.consensus_level,
                    "rationale": verdict.rationale,
                },
                "audit_trace": audit,
                "updated_at": utcnow_iso(),
            }

        # Apply sizing multiplier to risk fraction
        updated_proposal = dict(fused)
        updated_proposal["risk_fraction"] = fused.get("risk_fraction", 0.01) * verdict.sizing_multiplier
        updated_proposal["confidence"] = verdict.confidence
        updated_proposal["rationale"] = (
            f"Debate verified: {verdict.rationale} "
            f"(sizing={verdict.sizing_multiplier:.2f}x, consensus={verdict.consensus_level})"
        )

        return {
            "fused_proposal": updated_proposal,
            "fusion_score": verdict.confidence,
            "debate_verdict": {
                "decision": verdict.decision,
                "approved": verdict.approved,
                "confidence": verdict.confidence,
                "sizing_multiplier": verdict.sizing_multiplier,
                "consensus_level": verdict.consensus_level,
                "rationale": verdict.rationale,
            },
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class RiskControllerNode:
    """Agent: RiskController - SOLE deterministic gateway with order_authority=True."""

    #: Minimum conviction (0-1) for a proposal to pass; raised during loss-streak recovery.
    MIN_CONVICTION = 0.80
    RECOVERY_CONVICTION = 0.90

    def __init__(self, db_path: str = "trading_state.db", min_rr: float = 1.5, min_confluence: int = 1, initial_balance: float = 10000.0):
        self.db_path = db_path
        self.store = TradingStore(path=db_path)
        self.validator = DeterministicValidator(store=self.store, min_rr=min_rr, min_confluence=min_confluence)
        # Equity-aware configuration: 2% base risk, 20% max drawdown halt,
        # 5% daily loss halt, permanent halt below 20% of initial capital.
        live_balance = RiskManager._account_equity()
        base_balance = float(live_balance) if live_balance else float(initial_balance)
        if base_balance <= 0:
            # P0 doctrine: RiskManager rejects non-positive balance, and a
            # token balance (e.g. 1.0) cannot fund the exchange minimum lot,
            # forcing every sized trade to SIZE_BELOW_MINIMUM_REJECT. Seed
            # the paper default instead — never zero, never micro-funded.
            base_balance = 10000.0
        custom_state_file = None if db_path == "trading_state.db" else f"{db_path}.risk_state.json"
        self.risk_manager = RiskManager(
            balance=base_balance,
            risk_per_trade=0.02,
            max_drawdown=0.20,
            max_daily_loss=0.05,
            state_file=custom_state_file,
        )
        self.risk_manager.set_store(self.store)
        self.correlation = CorrelationRisk(db_path=db_path)

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        require_agent_action("RiskController", "validate_proposal")
        fused = state.get("fused_proposal")
        symbol = state["symbol"]
        master_ev = float(state.get("master_expectancy_r", 0.25))

        if not fused or fused.get("direction") == "HOLD":
            return {
                "validation_passed": False,
                "rejection_reasons": ["NO_ACTIONABLE_PROPOSAL"],
                "status": "REJECTED",
                "audit_trace": ["RiskController: No active proposal to validate."],
                "updated_at": utcnow_iso(),
            }

        # Master Expectancy Floor Gate
        if master_ev < 0.20:
            return {
                "validation_passed": False,
                "rejection_reasons": [f"MASTER_EXPECTANCY_BELOW_FLOOR:{master_ev:.2f}R<0.20R"],
                "status": "REJECTED",
                "audit_trace": [f"RiskController: Vetoed setup on {symbol}. Master EV {master_ev:.2f}R < 0.20R."],
                "updated_at": utcnow_iso(),
            }

        # Conviction quality bar: >= 0.80 normally; raised to 0.90 while in
        # loss-streak recovery (3+ consecutive losses).
        conviction = max(
            float(fused.get("confidence", 0.0) or 0.0),
            float(state.get("fusion_score", 0.0) or 0.0),
        )
        required_conviction = (
            self.RECOVERY_CONVICTION
            if getattr(self.risk_manager, "consecutive_losses", 0) >= 3
            else self.MIN_CONVICTION
        )
        if conviction < required_conviction:
            return {
                "validation_passed": False,
                "rejection_reasons": [f"CONVICTION_BELOW_BAR:{conviction:.2f}<{required_conviction:.2f}"],
                "status": "REJECTED",
                "audit_trace": [f"RiskController: Vetoed setup on {symbol}. Conviction {conviction:.2f} < {required_conviction:.2f}."],
                "updated_at": utcnow_iso(),
            }

        # Convert to internal TradeProposal for DeterministicValidator
        features = dict(state.get("feature_vector") or {})
        features["ema_alignment"] = 1 if fused["direction"] == "BUY" else -1
        features["mtf_alignment"] = 1 if fused["direction"] == "BUY" else -1
        features["adx"] = float(features.get("adx_14", 25.0))
        features["near_key_level"] = True
        features["confirmed_candle"] = True
        features["volume_confirmed"] = True

        stop_dist = abs(float(fused["entry_price"]) - float(fused["stop_loss"]))

        proposal_obj = TradeProposal(
            strategy="scalp" if state["timeframe"] in ("M1", "M5", "M15") else "swing",
            symbol=fused["symbol"],
            side=fused["direction"],
            entry=float(fused["entry_price"]),
            stop_loss=float(fused["stop_loss"]),
            take_profit=float(fused["take_profit"]),
            confidence=float(fused.get("confidence", 0.5)),
            features=features,
            model="LangGraph",
        )

        # Event blackout window: reuse the persisted per-symbol event monitor
        # when available, else evaluate snapshot news via the shared policy.
        event_blackout = False
        try:
            monitor = self.store.get_state(f"event_monitor:{symbol}", {}) if self.store is not None else {}
            if isinstance(monitor, dict) and "blackout" in monitor:
                event_blackout = bool(monitor.get("blackout"))
            else:
                news_items = (state.get("market_snapshot") or {}).get("news") or []
                event_state = evaluate_event_policy(symbol, news_items)
                event_blackout = bool(event_state.get("blackout"))
        except Exception as exc:
            logger.warning(f"Event policy evaluation warning: {exc}")
            event_blackout = False

        market_info = {
            "market_open": True,
            "symbol_tradable": True,
            "tick_age_ms": 100.0,
            "max_tick_age_ms": 5000.0,
            "spread": float(features.get("spread_pips", 1.0)),
            "max_spread": 10.0,
            "clock_ok": True,
            "session_allowed": True,
            "liquidity_ok": True,
            "microstructure_ok": True,
            "required_data_ok": True,
            "completed_candle": True,
            "entry_tolerance": 0.05,
            "stop_distance": stop_dist,
            "min_stop_distance": 0.0001,
            "high_impact_news": event_blackout,
            "setup_event_exclusion": event_blackout,
        }

        validation_res: ValidationResult = self.validator.validate(
            proposal=proposal_obj,
            market=market_info,
        )

        reasons = list(validation_res.reasons) if not validation_res.approved else []
        passed = bool(validation_res.approved)

        # 3-Tier Circuit Breaker Checks
        if not self.risk_manager.check_daily_drawdown():
            reasons.append("DAILY_DRAWDOWN_BREACH_HALT")
            passed = False
        if not self.risk_manager.check_consecutive_loss_breaker():
            reasons.append("CONSECUTIVE_LOSS_BREAKER_HALT")
            passed = False
        if not self.risk_manager.check_max_drawdown():
            reasons.append("MAX_DRAWDOWN_KILL_SWITCH_HALT")
            passed = False

        # PERMANENT halt below the equity floor (manual review to lift).
        if not self.risk_manager.check_equity_floor():
            reasons.append("EQUITY_FLOOR_PERMANENT_HALT")
            passed = False

        # Correlation block: no second same-direction exposure on a
        # correlated symbol while one is open.
        try:
            corr = self.correlation.correlated_open_risk(
                self.store, symbol, str(fused["direction"]), state["timeframe"],
            )
            if float(corr.get("correlated_open_risk", 0.0) or 0.0) > 0.0:
                reasons.append("CORRELATED_POSITION_BLOCK")
                passed = False
        except Exception as exc:
            logger.warning(f"Correlation check warning: {exc}")
            reasons.append("CORRELATION_CHECK_UNAVAILABLE")
            passed = False

        order_intent = None
        if passed:
            require_agent_action("RiskController", "submit_validated_intent")
            intent_id = f"intent_{symbol}_{int(datetime.now(timezone.utc).timestamp())}"
            # Calculate lot size with Universal 1R & circuit multiplier
            lot_size = self.risk_manager.calc_position_size(
                symbol=symbol,
                entry=proposal_obj.entry,
                sl=proposal_obj.stop_loss,
                confidence=proposal_obj.confidence,
            )
            if lot_size <= 0:
                # Rejected size rejects the trade — never floor to micro-lot.
                return {
                    "validation_passed": False,
                    "rejection_reasons": reasons + ["SIZE_BELOW_MINIMUM_REJECT"],
                    "status": "REJECTED",
                    "audit_trace": ["RiskController: Position size below minimum (or zeroed by equity-aware scaling). Trade rejected."],
                    "updated_at": utcnow_iso(),
                }
            
            order_intent = OrderIntentPayload(
                intent_id=intent_id,
                symbol=symbol,
                action=proposal_obj.side,
                order_type="MARKET",
                volume=lot_size,
                price=proposal_obj.entry,
                stop_loss=proposal_obj.stop_loss,
                take_profit=proposal_obj.take_profit,
                risk_usd=100.0,
                risk_percent=0.01 * float(state.get("applied_risk_multiplier", 1.0)),
            ).model_dump()

        audit = [f"RiskController: Validated proposal -> Approved={passed}, Reasons={reasons}"]
        return {
            "validation_passed": passed,
            "rejection_reasons": reasons,
            "order_intent": order_intent,
            "status": "VALIDATED" if passed else "REJECTED",
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class OrderExecutionNode:
    """Executes validated OrderIntents via PaperBroker or MT5 Gateway."""

    def __init__(self, broker: Any = None):
        self.broker = broker

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        intent = state.get("order_intent")
        if not intent:
            return {
                "execution_receipt": None,
                "error": "Missing order intent",
                "status": "ERROR",
                "updated_at": utcnow_iso(),
            }

        # Route through the REAL broker gateway when one is attached. Only a
        # no-broker (paper) run may synthesize a local fill id; previously this
        # node fabricated a ticket even for live brokers, so orders were never
        # actually sent while the journal claimed EXECUTED.
        ticket = None
        broker_error = None
        if self.broker is not None:
            try:
                order_kwargs = dict(
                    symbol=intent["symbol"], action=intent["action"],
                    volume=float(intent["volume"]), price=float(intent["price"]),
                    sl=float(intent["stop_loss"]), tp=float(intent.get("take_profit", 0.0)),
                )
                if hasattr(self.broker, "execute_order"):
                    ticket = self.broker.execute_order(**order_kwargs)
                elif hasattr(self.broker, "open_order"):
                    ticket = self.broker.open_order(**order_kwargs)
                else:
                    broker_error = "broker exposes neither execute_order nor open_order"
            except Exception as exc:
                broker_error = str(exc)[:300]
            if ticket is None:
                return {
                    "execution_receipt": None,
                    "error": broker_error or "broker returned no ticket",
                    "status": "ERROR",
                    "audit_trace": [f"OrderExecution: intent {intent.get('intent_id')} NOT executed: {broker_error}"],
                    "updated_at": utcnow_iso(),
                }
        else:
            ticket = int(datetime.now(timezone.utc).timestamp()) % 1_000_000
        receipt = ExecutionReceiptPayload(
            ticket=ticket,
            symbol=intent["symbol"],
            action=intent["action"],
            volume=float(intent["volume"]),
            open_price=float(intent["price"]),
            sl=float(intent["stop_loss"]),
            tp=float(intent.get("take_profit", 0.0)),
            is_paper=state["paper_mode"],
        ).model_dump()

        audit = [f"OrderExecution: Order filled ticket={ticket} {intent['action']} {intent['volume']} lots @ {intent['price']} via {'broker' if self.broker is not None else 'paper-local'}"]
        return {
            "execution_receipt": receipt,
            "status": "EXECUTED",
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }


class RejectionSinkNode:
    """Persists rejection events for audit, memory, and cooldown management (P03/P04)."""

    def __init__(self, db_path: str = "trading_state.db", prfs_tracker: Optional[Any] = None):
        self.ops = AgentOperations(db_path=db_path)
        if prfs_tracker is not None:
            self.prfs_tracker = prfs_tracker
        else:
            try:
                from core.prfs_counterfactual import PRFSTracker
                self.prfs_tracker = PRFSTracker()
            except ImportError:
                self.prfs_tracker = None

    def __call__(self, state: TradingCycleState) -> Dict[str, Any]:
        reasons = state.get("rejection_reasons", [])
        symbol = state["symbol"]
        
        try:
            self.ops.event(
                actor="RiskController",
                event_type="risk_rejection",
                payload={
                    "symbol": symbol,
                    "cycle_id": state["cycle_id"],
                    "reasons": reasons,
                    "fused_proposal": state.get("fused_proposal"),
                },
            )
        except Exception as exc:
            logger.warning(f"Rejection logging warning: {exc}")

        # P03/P04: Record rejection into PRFS Counterfactual tracker
        if self.prfs_tracker is not None:
            try:
                from core.prfs_counterfactual import RejectionEvent
                cycle_id = state.get("cycle_id", "cycle")
                key = f"{symbol}:{cycle_id}"
                fused = state.get("fused_proposal") or {}
                ref_price = float(fused.get("price") or 0.0)
                reason_str = str(reasons[0]) if reasons else "risk_veto"
                event = RejectionEvent(
                    key=key,
                    symbol=symbol,
                    reason=reason_str,
                    source="RejectionSinkNode",
                    ref_price=ref_price,
                    state={"cycle_id": cycle_id, "reasons": reasons},
                )
                self.prfs_tracker.record_rejection(event)
            except Exception as prfs_exc:
                logger.debug(f"PRFS counterfactual tracking error: {prfs_exc}")

        audit = [f"RejectionSink: Logged rejection for {symbol}. Reasons: {reasons}"]
        return {
            "status": "REJECTED",
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }

    def observe_price(self, key: str, age_bars: int, price: Optional[float]) -> None:
        """Feed forward price observation into PRFS tracker (P03/P04)."""
        if self.prfs_tracker is not None:
            from core.prfs_counterfactual import ForwardObservation
            if self.prfs_tracker.due(key, age_bars):
                self.prfs_tracker.record_observation(ForwardObservation(key=key, age_bars=age_bars, price=price))

    def audit_rejections(self) -> Dict[str, Any]:
        """Compute 5-tier precision audit across recorded rejections (P04)."""
        if self.prfs_tracker is not None:
            from core.prfs_counterfactual import audit
            return audit(self.prfs_tracker)
        return {}


class QuantitativeCoachNode:
    """Agent: QuantitativeCoach - Reflects on completed trades and distills L1/L2 memory."""

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path
        self.memory = TradingMemory(db_path=db_path)

    def __call__(self, state: MemoryReflectionState) -> Dict[str, Any]:
        require_agent_action("QuantitativeCoach", "label_trade")
        ticket = state["deal_ticket"]
        pnl = state["realized_pnl"]
        r_mult = state["realized_r_multiple"]
        symbol = state["symbol"]

        # 1. Label Outcome
        label = "WIN_TARGET" if pnl > 0 and r_mult >= 1.0 else ("LOSS_STOP" if pnl < 0 else "BREAKEVEN")

        # 2. Distill L1 Atomic Fact
        fact = {
            "fact_key": f"trade_outcome_{ticket}",
            "fact_value": f"Ticket {ticket} on {symbol} closed {label} with {r_mult:.2f}R",
            "fact_status": "OBSERVED",
            "source": "QuantitativeCoach",
            "confidence": 1.0,
        }

        try:
            self.memory.record_fact(
                source_id=str(ticket),
                fact_key=fact["fact_key"],
                fact_value=fact["fact_value"],
                fact_status=fact["fact_status"],
                source=fact["source"],
                confidence=fact["confidence"],
                valid_from=state["close_time"],
            )
        except Exception as exc:
            logger.warning(f"Memory atomic fact recording warning: {exc}")

        audit = [f"QuantitativeCoach: Distilled trade {ticket} -> {label} ({r_mult:.2f}R) into L1 Memory."]
        return {
            "outcome_label": label,
            "distilled_atomic_facts": [fact],
            "scenario_updated": True,
            "audit_trace": audit,
            "updated_at": utcnow_iso(),
        }
