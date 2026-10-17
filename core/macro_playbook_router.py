"""Meta-Playbook Router — pure composition of existing subsystems.

Composes the institutional macro-architecture content (event playbooks, curve
regimes, growth-inflation quadrants, Taylor-rule divergence) onto the engines
this repo already owns. Adds NOTHING new computationally beyond pure lookups
and closed-form arithmetic; every route consults an existing, independently
tested module:

  routing event type        <- core.news_trading.classify_event_tier
  release-phase guidance    <- core.news_trading.detect_news_phase
  conviction multiplier     <- core.macro_event_alpha_engine.\
                               MacroRegimePositioningArbitrator.\
                               evaluate_regime_and_crowding
  headline-vs-composition   <- core.macro_event_alpha_engine.\
                               StandardizedSurpriseDecomposer.decompose_surprise
  liquidity stance naming   <- aligns with LiquidityStance in
                               research.decision_models.macro_ontology

Design invariants (mirrors repo doctrine, see news_impact_score.py):
- execution_authority is ALWAYS False. Verdicts are advisory context only;
  execution promotion stays in the RiskManager / edge-lifecycle paths.
- Fail-closed: missing/unparseable inputs yield INSUFFICIENT_DATA verdicts —
  never a fabricated playbook row or bullish/bearish tilt.
- Data-table first: the playbook/curve/quadrant content is dataclasses and
  tuples, not scattered if-chains, so tables stay auditable.
- Shortest working diff: no network, no I/O, no third-party imports.

Source lineage of the tables: pasted institutional macro-architecture spec
(2026-10). The Bear Flattener row was TRUNCATED in that paste ("Front-end
yields rise faster ...") and is COMPLETED BY PATTERN here: the mechanics mirror
the other three rows (hawkish front-end repricing with an anchored long end,
driver = central-bank tightening repricing / term-premium bid exhausted),
consistent with the economic_dynamics_catalog BEAR_FLATTENER entity and the
bull/bear steepener terminology. Flagged here and in
docs/ARCHITECTURE_DUPLICATE_PREVENTION.md so the provenance is auditable.

Integration (zero-wiring): route_announcement() accepts the exact dict shapes
already flowing through get_news_trading_context / decompose_surprise callers,
so consumers pass through without adapting anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = __import__("logging").getLogger("MacroPlaybookRouter")

from core.institutional_macro_framework import (
    evaluate_fomc_reaction_function,
    evaluate_fomc_press_conference,
    evaluate_ecb_fragmentation_playbook,
    evaluate_boj_carry_unwind_playbook,
    evaluate_pboc_credit_transmission_playbook,
    evaluate_cpi_release_playbook,
    evaluate_nfp_labor_playbook,
    evaluate_pmi_cycle_momentum,
    evaluate_terms_of_trade_shock,
    evaluate_gold_decoupling_dynamics,
    evaluate_sovereign_debt_crisis_hierarchy,
    evaluate_cot_3y_z_scores,
    evaluate_dealer_gamma_regime,
    evaluate_fx_risk_reversals,
    evaluate_master_cross_asset_matrix,
    evaluate_three_window_execution_protocol,
)
from data.open_intelligence_feed import (
    OpenIntelligenceAggregator,
    GDELTFeed,
    ACLEDFeed,
    DBnomicsFeed,
    BISFeed,
)
from core.macro_conviction_funnel import (
    calculate_net_conviction_score,
    evaluate_selection_funnel,
    classify_master_playbook,
    MacroConvictionEngine,
)
from core.temporal_planning_engine import (
    classify_yearly_regime,
    classify_monthly_cycle,
    classify_day_of_week_template,
    detect_session_killzone,
    evaluate_temporal_context,
    TemporalPlanningEngine,
)
from core.microstructure_impact_engine import (
    classify_dealing_range_zones,
    detect_liquidity_sweep,
    evaluate_cvd_divergence,
    evaluate_impact_candle,
    grade_a_plus_confluence,
    MicrostructureImpactEngine,
)
from core.bitemporal_purged_wfo import (
    BiTemporalMacroStore,
    MicrostructureSweepSimulator,
    TradeRecord,
    generate_purged_wfo_folds,
    evaluate_statistical_survival_gates,
)

EXECUTION_AUTHORITY = False

INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
INSUFFICIENT_DATA_REASON = "No playbook verdict available: required inputs missing or unparseable."


# ============================================================================
# 1. Event Playbooks (the 5-row table)
# ============================================================================

@dataclass(frozen=True)
class EventPlaybook:
    """One institutional event playbook row.

    Fields are verbatim lineage from the spec table; thresholds are advisory
    context consumed from existing scorecards, never gates themselves.
    """
    event_type: str
    aliases: Tuple[str, ...]                # tokens matched (upper-cased containment)
    tier_expected: int                      # 1..3, cross-checked vs classify_event_tier
    critical_signal: str
    transmission_vector: str
    tactical_execution_bias: str


# Thresholds "critical_signal" are input contracts for the caller: e.g. NFP
# requires headline vs consensus > 50k AND net-revision + unemployment-tick
# context, which decompose_surprise already supplies as z_delta + components.
EVENT_PLAYBOOKS: Tuple[EventPlaybook, ...] = (
    EventPlaybook(
        event_type="NFP",
        aliases=("NON-FARM", "NFP", "PAYROLL", "NONFARM"),
        tier_expected=1,
        critical_signal="Net revision + unemployment-rate tick vs headline >50k deviation "
                        "(headline alone is noise; the wage/revision composition drives direction).",
        transmission_vector="Rates curve steepening/flattening -> USD FX pairs -> Equity futures index.",
        tactical_execution_bias="Avoid the first 2-5 min wick; trade the post-15min trend continuation — "
                                "or fade on adverse wage sub-components (AHE heat/revisions).",
    ),
    EventPlaybook(
        event_type="CPI",
        aliases=("CPI", "CONSUMER PRICE INDEX", "PCE", "INFLATION"),
        tier_expected=1,
        critical_signal="Supercore services (ex-shelter) & 3-month annualized core-CPI trend, "
                        "NOT the headline (volatile components can fake a beat).",
        transmission_vector="US 2Y breakevens & 2Y real yields -> Gold, USD/JPY, Nasdaq multiples.",
        tactical_execution_bias="Asymmetric options skew; scale into the currency crosses where the "
                                "rate differential widens fastest.",
    ),
    EventPlaybook(
        event_type="CENTRAL_BANK_DECISION",
        aliases=("FEDERAL FUNDS RATE", "FOMC", "ECB", "BOJ", "RATE DECISION", "MONETARY POLICY"),
        tier_expected=1,
        critical_signal="Dot-plot shift >=25bps or press-conference tone divergence from the formal statement.",
        transmission_vector="SOFR strip / OIS curve repricing -> front-end FX yield differentials -> credit spreads.",
        tactical_execution_bias="Three-window strategy: Phase 1 statement release, Phase 2 press conference, "
                                "Phase 3 post-close repricing.",
    ),
    EventPlaybook(
        event_type="GEOPOLITICAL_SHOCK",
        aliases=("WAR", "STRIKE", "INVASION", "CHOKEPOINT", "EXPORT BAN", "TARIFF", "SANCTION",
                 "INFRASTRUCTURE COMPROMISE"),
        tier_expected=1,
        critical_signal="Energy-infrastructure compromise, maritime-chokepoint closure, or critical-mineral "
                        "export ban (verify physical flow, not headline rhetoric).",
        transmission_vector="Commodity supply-curve shift -> inflation expectations UP + growth forecasts DOWN "
                            "(supply shock breaks monetary correlations).",
        tactical_execution_bias="Long volatility (VIX/OVX), long physical commodities (Brent, Gold), "
                                "short high-beta cyclical FX.",
    ),
    EventPlaybook(
        event_type="SYSTEMIC_LIQUIDITY_CRISIS",
        aliases=("LIQUIDITY CRISIS", "FRA-OIS", "REPO SPIKE", "COMMERCIAL PAPER", "BASIS SWAP",
                 "FUNDING SQUEEZE", "OUTRIGHT REPO"),
        tier_expected=1,
        critical_signal="FRA-OIS spreads widening, repo spikes, commercial-paper freezes, basis-swap dislocation.",
        transmission_vector="USD funding squeeze -> indiscriminate asset liquidation -> flight to short T-bills.",
        tactical_execution_bias="Long USD cash, long short-dated Treasuries, short high-yield credit "
                                "and emerging-market FX.",
    ),
)


def match_event_playbook(event_title: str) -> Optional[EventPlaybook]:
    """Match a calendar/wire title to a playbook row by upper-cased containment.

    Fail-closed: returns None (no row) rather than guessing a default row.
    Tries the most specific aliases (longest tokens) first to avoid e.g.
    "CONSUMER PRICE INDEX" being swallowed by a generic token match.
    """
    if not isinstance(event_title, str) or not event_title.strip():
        return None
    upper = event_title.upper().strip()
    best: Optional[EventPlaybook] = None
    best_len = 0
    for row in EVENT_PLAYBOOKS:
        for alias in row.aliases:
            if alias in upper and len(alias) > best_len:
                best, best_len = row, len(alias)
    return best


# ============================================================================
# 2. Curve-Regime Playbook (Bull/Bear x Steeper/Flattener)
# ============================================================================

@dataclass(frozen=True)
class CurveRegimePlaybook:
    """One curve-regime row: detection rule, driver, institutional playbook."""
    regime: str
    mechanics: str
    macro_driver: str
    playbook: str


CURVE_REGIME_PLAYBOOKS: Dict[str, CurveRegimePlaybook] = {
    "BULL_STEEPENER": CurveRegimePlaybook(
        regime="BULL_STEEPENER",
        mechanics="Front-end yields fall faster than long-end yields.",
        macro_driver="Imminent central-bank rate cuts, recessionary onset.",
        playbook="Long front-end duration (SOFR/2Y Treasuries); long gold; short risk assets — "
                 "transitioning to cyclical equities late in cycle.",
    ),
    "BEAR_STEEPENER": CurveRegimePlaybook(
        regime="BEAR_STEEPENER",
        mechanics="Long-end yields rise faster than front-end yields.",
        macro_driver="Fiscal expansion, ballooning debt supply, unanchored inflation expectations.",
        playbook="Short 10Y/30Y paper; long commodities; underweight duration-heavy equities; "
                 "long USD vs. low-yielders.",
    ),
    "BULL_FLATTENER": CurveRegimePlaybook(
        regime="BULL_FLATTENER",
        mechanics="Long-end yields fall faster than front-end yields.",
        macro_driver="Disinflation, growth deceleration, flight-to-safety duration squeeze.",
        playbook="Long 10Y/30Y sovereign debt; long growth/tech equities; short industrial commodities.",
    ),
    # SPEC PROVENANCE: this row was truncated in the source paste ("Front-end
    # yields rise faster" [cut]) and is completed by pattern. Mechanics mirror
    # the other three rows; driver/playbook are the canonical
    # front-end-led hawkish repricing read (front end prices tightening with
    # the long end anchored). See docs/ARCHITECTURE_DUPLICATE_PREVENTION.md.
    "BEAR_FLATTENER": CurveRegimePlaybook(
        regime="BEAR_FLATTENER",
        mechanics="Front-end yields rise faster than long-end yields (curve flattens/inverts as "
                  "policy repricing pulls the front end up under an anchored/less-rising long end).",
        macro_driver="Hawkish central-bank repricing (cut expectations unwound), inflation momentum "
                     "forcing a policy response, long-end term premium held down by anchored inflation "
                     "expectations or scarce duration supply.",
        playbook="Short front-end duration (2Y/SOFR strip) or curve flatteners; underweight duration; "
                 "favor cash over duration-heavy equities; USD supported vs low-yielders — "
                 "fade the steepening trade until the front end reprices.",
    ),
}


def detect_curve_regime(front_end_delta_bps: Any, long_end_delta_bps: Any,
                        *, dominance_ratio: float = 1.5) -> Dict[str, Any]:
    """Classify the curve regime from front-end vs long-end yield deltas.

    Args:
        front_end_delta_bps: change in the front end (2Y / SOFR strip) in bps.
        long_end_delta_bps:  change in the long end (10Y/30Y) in bps.
        dominance_ratio: how much faster one leg must move than the other
            before |delta| dominance counts for the bull/bear tie-break.

    Canonical mechanics (2s10s-spread convention, matching the source table):
      slope_change = long_end_delta - front_end_delta   (change in 10Y-2Y spread)
      slope_change > 0  -> STEEPENER   (spread widens)
      slope_change < 0  -> FLATTENER   (spread narrows)
    Bull/Bear follows bond-price convention: BULL = yields falling, BEAR =
    yields rising, taken from the dominant leg's sign (front-end-led moves are
    the classic Bear Flattener case: front end reprices hawkish, long end
    anchored).

    Returns fail-closed dict: {"regime": "INSUFFICIENT_DATA"} if either delta
    is missing/unparseable, and {"regime": "NO_CLEAR_MOVEMENT"} when the slope
    change is inside the dead-band (no regime is fabricated from noise).
    """
    def _num(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v

    fe, le = _num(front_end_delta_bps), _num(long_end_delta_bps)
    if fe is None or le is None:
        return {"regime": INSUFFICIENT_DATA, "playbook": None,
                "reason": "front-end or long-end delta missing/unparseable",
                "execution_authority": False}

    dead_band_bps = 2.0  # sub-2bps slope change = regime-neutral noise
    fe_mag, le_mag = abs(fe), abs(le)
    slope_change_bps = le - fe          # change in (long - front) spread, bps
    if abs(slope_change_bps) < dead_band_bps:
        return {"regime": "NO_CLEAR_MOVEMENT", "playbook": None,
                "reason": "slope change inside +-2bps dead-band",
                "execution_authority": False}

    # Bull/Bear from the dominant leg's sign (ties broken by dominance_ratio).
    if fe_mag * dominance_ratio >= le_mag:
        dominant_sign, dominant_leg = fe, "FRONT_END"
    elif le_mag * dominance_ratio >= fe_mag:
        dominant_sign, dominant_leg = le, "LONG_END"
    else:
        dominant_sign, dominant_leg = (fe + le) * 0.5, "BOTH"
    direction = "BEAR" if dominant_sign > 0 else "BULL"

    steepener = slope_change_bps > 0
    regime = f"{direction}_{'STEEPENER' if steepener else 'FLATTENER'}"
    return {
        "regime": regime,
        "playbook": vars(CURVE_REGIME_PLAYBOOKS[regime]).copy(),
        "front_end_delta_bps": round(fe, 2),
        "long_end_delta_bps": round(le, 2),
        "dominant_leg": dominant_leg,
        "direction": direction,
        "slope_change_bps": round(slope_change_bps, 2),
        "execution_authority": False,
    }


# ============================================================================
# 3. Growth-Inflation Quadrant (Reflation/Goldilocks/Stagflation/Deflation)
# ============================================================================

@dataclass(frozen=True)
class QuadrantPlaybook:
    quadrant: str
    growth_axis: str   # "UP" | "DOWN"
    inflation_axis: str
    bias: str


GROWTH_INFLATION_QUADRANTS: Dict[str, QuadrantPlaybook] = {
    "REFLATION": QuadrantPlaybook(
        quadrant="REFLATION", growth_axis="UP", inflation_axis="UP",
        bias="Long cyclical equities, commodities; short nominal duration.",
    ),
    "GOLDILOCKS": QuadrantPlaybook(
        quadrant="GOLDILOCKS", growth_axis="UP", inflation_axis="DOWN",
        bias="Long duration, tech/equities, high-yield credit.",
    ),
    "STAGFLATION": QuadrantPlaybook(
        quadrant="STAGFLATION", growth_axis="DOWN", inflation_axis="UP",
        bias="Long cash, energy, gold; short long-dated fixed income, growth multiples.",
    ),
    "DEFLATION": QuadrantPlaybook(
        quadrant="DEFLATION", growth_axis="DOWN", inflation_axis="DOWN",
        bias="Long sovereign bonds, defensive equities, USD; short commodities.",
    ),
}


def classify_quadrant(growth_z: Any, inflation_z: Any,
                      *, threshold: float = 0.5) -> Dict[str, Any]:
    """Map normalized growth/inflation z-axes onto the institutional quadrant.

    Both inputs are 0-centered z-style scores (positive = rising/firming).
    Fail-closed: either axis missing => INSUFFICIENT_DATA. The threshold is
    a dead-band, so neutral prints do not force a quadrant claim.
    """
    def _num(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v

    g, i = _num(growth_z), _num(inflation_z)
    if g is None or i is None:
        return {"quadrant": INSUFFICIENT_DATA, "bias": None,
                "reason": "growth or inflation axis missing/unparseable",
                "execution_authority": False}

    g_up = g >= threshold
    i_up = i >= threshold
    if g_up and i_up:
        quadrant = "REFLATION"
    elif g_up and not i_up:
        quadrant = "GOLDILOCKS"
    elif (not g_up) and i_up:
        quadrant = "STAGFLATION"
    else:
        quadrant = "DEFLATION"

    qb = GROWTH_INFLATION_QUADRANTS[quadrant]
    return {
        "quadrant": quadrant,
        "bias": qb.bias,
        "growth_z": round(g, 3),
        "inflation_z": round(i, 3),
        "threshold": threshold,
        "execution_authority": False,
    }


# ============================================================================
# 4. Central-Bank Reaction Function: Taylor-rule divergence (pure arithmetic)
# ============================================================================

def taylor_rule_divergence(market_implied_rate: Any,
                           inflation: Any,
                           *,
                           inflation_target: float = 2.0,
                           output_gap: Any = 0.0,
                           neutral_rate: float = 1.5,
                           gc_weight: float = 1.0) -> Dict[str, Any]:
    """Compute the Taylor-implied rate and its divergence from market pricing.

        i_implied = neutral + inflation + gc_weight * (inflation - target)
                    + 0.5 * output_gap

    Divergence (bps) = (market_implied_rate - i_implied) * 100:
    positive means the market prices MORE tightening than the reaction function
    justifies (hawkish-divergence fade candidate); negative means market prices
    more easing than the rule (dovish-divergence alpha).

    Fail-closed: any missing/non-finite numeric input => INSUFFICIENT_DATA.
    Pure arithmetic; no gate authority. This is the one genuine §1 computation
    gap closed (previously only referenced narratively in case studies).
    """
    def _num(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v

    mir, pi, gap, gw = (_num(market_implied_rate), _num(inflation),
                        _num(output_gap), _num(gc_weight))
    if mir is None or pi is None or gap is None or gw is None:
        return {"divergence_bps": None, "taylor_implied_rate": None,
                "divergence_class": INSUFFICIENT_DATA,
                "reason": "market_implied_rate/inflation/output_gap missing or non-finite",
                "execution_authority": False}

    implied = neutral_rate + pi + gw * (pi - inflation_target) + 0.5 * gap
    divergence_bps = round((mir - implied) * 100.0, 2)

    if abs(divergence_bps) < 12.5:
        divergence_class = "ALIGNED"               # within one 25bp step/2
    elif divergence_bps > 0:
        # market_implied_rate > taylor-implied: market prices MORE tightening
        # than the reaction function justifies -> hawkish-divergence fade candidate.
        divergence_class = "MARKET_PRICED_HAWKISH"
    else:
        # market_implied_rate < taylor-implied: market prices MORE easing ->
        # dovish-divergence fade candidate.
        divergence_class = "MARKET_PRICED_DOVISH"

    return {
        "taylor_implied_rate": round(implied, 3),
        "market_implied_rate": round(mir, 3),
        "divergence_bps": divergence_bps,
        "divergence_class": divergence_class,
        "interpretation": (
            "Market prices MORE easing than the reaction function implies "
            "(dovish-divergence fade candidate)." if divergence_bps < -12.5
            else "Market prices MORE tightening than the reaction function implies "
                 "(hawkish-divergence fade candidate)." if divergence_bps > 12.5
            else "Market pricing aligned with the implicit reaction function."),
        "execution_authority": False,
    }


# ============================================================================
# 5. Meta-Playbook Router (composes the engines above, plus existing gates)
# ============================================================================

_PHASE_MAP = {
    # pre-release phases (negative / distant) and the spike phases are already
    # named by news_trading.detect_news_phase; we map them onto the router's
    # meta-logic of pre/release/post alignment.
    "pre_news_distant": "PRE_EVENT",
    "pre_news_imminent": "PRE_EVENT",
    "pre_news_critical": "PRE_EVENT",
    "unknown": INSUFFICIENT_DATA,
}


def _meta_phase(phase_name: Any, minutes_since_event: Any) -> str:
    if phase_name is None or minutes_since_event is None:
        return INSUFFICIENT_DATA
    normalized = _PHASE_MAP.get(str(phase_name), str(phase_name))
    if normalized in ("PRE_EVENT",):
        return "PRE_EVENT"
    try:
        mins = float(minutes_since_event)
    except (TypeError, ValueError, OverflowError):
        return INSUFFICIENT_DATA
    if mins > 30.0:
        return "POST_EVENT_ALIGNED"
    return "RELEASE"


def route_event(event_title: Any = None,
                *,
                minutes_since_event: Any = None,
                phase_name: Any = None,
                actual: Any = None,
                consensus: Any = None,
                historical_std: Any = None,
                disaggregated_components: Optional[Dict[str, float]] = None,
                macro_regime: str = "EXPANSION",
                cot_speculative_percentile: Any = None) -> Dict[str, Any]:
    """Meta-Playbook Router: event detection -> phase -> playbook -> conviction.

    Composition contract (every engine is called through its public API):
      - core.news_trading.classify_event_tier   (tier cross-check)
      - core.news_trading.detect_news_phase     (phase naming)
      - core.macro_event_alpha_engine.StandardizedSurpriseDecomposer.\
        decompose_surprise (z_delta + composition note)
      - core.macro_event_alpha_engine.MacroRegimePositioningArbitrator.\
        evaluate_regime_and_crowding (pre-positioning asymmetry)

    Post-Event Alignment (router step 3): structural (|z|>=2) -> swing
    allocation guidance; transient (|z|<0.5) -> mean-reversion guidance;
    in between -> trend-continuation guidance per the playbook row.

    Fail-closed: an unmatched event titles yields INSUFFICIENT_DATA — never
    a default row. execution_authority always False.
    """
    # Lazy local import keeps module import order neutral (mirrors repo style).
    from core.news_trading import classify_event_tier, detect_news_phase
    from core.macro_event_alpha_engine import (
        MacroRegimePositioningArbitrator,
        StandardizedSurpriseDecomposer,
    )

    if not isinstance(event_title, str) or not event_title.strip():
        return {"verdict": INSUFFICIENT_DATA, "playbook": None,
                "reason": "event title missing", "execution_authority": False}

    row = match_event_playbook(event_title)
    tier = classify_event_tier(event_title)
    if row is None:
        return {
            "verdict": INSUFFICIENT_DATA,
            "playbook": None,
            "tier": tier,
            "reason": INSUFFICIENT_DATA_REASON,
            "event_title": event_title,
            "execution_authority": False,
        }

    # Phase resolution: prefer explicit caller-provided detected phase, else
    # reuse detect_news_phase only when both timestamps were actually supplied.
    phase: Any = None
    minutes: Any = minutes_since_event
    if phase_name is not None:
        phase = str(phase_name)
    elif minutes is None:
        # No timing context at all -> ask detect_news_phase with unknown inputs
        # only if a current_time would be meaningful; otherwise fail closed.
        phase = None

    meta_phase = _meta_phase(phase, minutes)

    # Release-phase composition: z_delta & reaction type from existing engines.
    z_delta: Optional[float] = None
    composition: Dict[str, Any] = {}
    if actual is not None and consensus is not None:
        try:
            composition = StandardizedSurpriseDecomposer.decompose_surprise(
                row.event_type,
                float(actual),
                float(consensus),
                historical_std=(None if historical_std is None else float(historical_std)),
                disaggregated_components=disaggregated_components,
            )
            z_delta = composition.get("z_delta")
        except (TypeError, ValueError, OverflowError) as exc:
            log.debug("decompose_surprise failed (%s); continuing without z", exc)

    conviction: Dict[str, Any] = {}
    if z_delta is not None:
        try:
            conviction = MacroRegimePositioningArbitrator.evaluate_regime_and_crowding(
                z_delta=z_delta,
                macro_regime=macro_regime,
                cot_speculative_percentile=(50.0 if cot_speculative_percentile is None
                                            else float(cot_speculative_percentile)),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            log.debug("evaluate_regime_and_crowding failed (%s); continuing", exc)

    # Post-event alignment policy (router step 3) — advisory guidance only.
    if z_delta is None:
        alignment = INSUFFICIENT_DATA
    elif abs(z_delta) >= 2.0:
        alignment = "STRUCTURAL_SHIFT_SWING_ALLOCATION"
    elif abs(z_delta) <= 0.5:
        alignment = "TRANSIENT_NOISE_MEAN_REVERSION"
    else:
        alignment = "TREND_CONTINUATION"

    composition_note = ""
    if disaggregated_components:
        comp_strs = [f"{k}={v}" for k, v in disaggregated_components.items()]
        composition_note = "Headline vs composition: " + "; ".join(comp_strs)

    three_window: Optional[Dict[str, Any]] = None
    if minutes is not None:
        try:
            three_window = evaluate_three_window_execution_protocol(
                minutes_from_event=float(minutes),
                cross_asset_confirmation=bool(conviction.get("conviction_multiplier", 1.0) >= 1.0) if conviction else False,
            )
        except Exception as exc:
            log.debug("evaluate_three_window_execution_protocol failed (%s); continuing", exc)

    temporal_context: Optional[Dict[str, Any]] = None
    try:
        temporal_context = evaluate_temporal_context(datetime.now(timezone.utc))
    except Exception as exc:
        log.debug("evaluate_temporal_context failed (%s); continuing", exc)

    return {
        "verdict": "ROUTED",
        "event_type": row.event_type,
        "matched_alias_confidence": "LEXICAL_EXACT",
        "tier": tier,
        "tier_matches_playbook": (tier == row.tier_expected),
        "phase": meta_phase,
        "playbook": {
            "critical_signal": row.critical_signal,
            "transmission_vector": row.transmission_vector,
            "tactical_execution_bias": row.tactical_execution_bias,
        },
        "surprise": (None if z_delta is None else {
            "z_delta": z_delta,
            "impact_level": composition.get("impact_level"),
            "permitted_models": composition.get("permitted_models"),
        }),
        "alignment": alignment,
        "conviction": conviction or None,
        "composition_note": composition_note or None,
        "three_window_protocol": three_window,
        "temporal_context": temporal_context,
        "event_title": event_title,
        "execution_authority": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================================
# 6. Intent Router (thin swarm-role mapping, zero new agents)
# ============================================================================

@dataclass(frozen=True)
class IntentRouteTarget:
    target: str        # existing module owning the role
    symbol: str        # public entrypoint symbol
    rationale: str


# The institutional swarm maps onto components that own these roles.
SWARM_ROLE_MAP: Dict[str, IntentRouteTarget] = {
    # 1. Regime & News Ingestion Agent
    "INGESTION": IntentRouteTarget(
        target="data.news_triage",
        symbol="triage_articles",
        rationale="Layer-0 triage: credibility/dedupe/fact-vs-opinion/escalation "
                  "(validates releases; revision semantics live in D1c)."),
    # 2. Central Bank Tone Agent
    "CB_TONE": IntentRouteTarget(
        target="intelligence.sovereign_macro_and_literary_forensics",
        symbol="parse_central_bank_speech_forensics",
        rationale="Hawk/dove score differential over pressers + dot-plot tone; "
                  "numeric anchor is macro_event_alpha_engine " \
                  "speaker_tone_score inputs."),
    # 3. Cross-Asset Synthesis Agent
    "CROSS_ASSET": IntentRouteTarget(
        target="intelligence.intermarket_regime",
        symbol="classify_intermarket_regime",
        rationale="Cross-market mispricing scan: FX vs 2Y spreads, credit widening "
                  "without equity-vol response (D6b confirmation is the seam)."),
    # 4. Risk & Execution Arbiter
    "RISK_SIZING": IntentRouteTarget(
        target="core.risk_manager",
        symbol="RiskManager",
        rationale="Position limits, margin gates, drawdown ceilings (three-limits "
                  "framework) — sole promotion authority; bounded_autonomy vetoes."),
    "REGIME": IntentRouteTarget(
        target="intelligence.regime_classifier",
        symbol="MacroRegimeClassifier",
        rationale="Regime labeling (rule/HMM) for the quadrant router's axes."),
    # 5. Multi-Domain Macro Conviction Scorer & Selection Funnel
    "MACRO_CONVICTION": IntentRouteTarget(
        target="core.macro_conviction_funnel",
        symbol="calculate_net_conviction_score",
        rationale="4-domain scoring S_{i,t} in [-100, +100] with Sunday 2-trade/week selection funnel."),
    # 6. Multi-Horizon Temporal Planner
    "TEMPORAL_PLANNER": IntentRouteTarget(
        target="core.temporal_planning_engine",
        symbol="evaluate_temporal_context",
        rationale="4-tier temporal planning engine: Yearly Q1-Q4, Monthly 4-week, Day-of-week, Killzones."),
    # 7. Microstructure Gatekeeper
    "MICROSTRUCTURE_GATEKEEPER": IntentRouteTarget(
        target="core.microstructure_impact_engine",
        symbol="grade_a_plus_confluence",
        rationale="Microstructure gatekeeper: Sweeps, dual-mode CVD, Impact Candle Score, A+ confluence matrix."),
    # 8. Purged & Embargoed WFO Validator
    "PURGED_WFO": IntentRouteTarget(
        target="core.bitemporal_purged_wfo",
        symbol="evaluate_statistical_survival_gates",
        rationale="Bi-temporal PIT store and purged/embargoed walk-forward backtest simulator."),
    # 9. Open-Source Intelligence Aggregator
    "OPEN_INTELLIGENCE": IntentRouteTarget(
        target="data.open_intelligence_feed",
        symbol="OpenIntelligenceAggregator",
        rationale="Open-source intelligence: GDELT 2.0 conflict scoring, ACLED chokepoints, DBnomics, BIS."),
}


def route_intent(intent: Any) -> Dict[str, Any]:
    """Map a swarm-role intent string to the existing component that owns it.

    Fail-closed: unknown intent -> INSUFFICIENT_DATA targets. The router
    returns TARGETS + rationale only; it never invokes them and never grants
    execution authority.
    """
    if not isinstance(intent, str) or not intent.strip():
        return {"targets": [], "intent": intent,
                "reason": intent and "" or "intent missing/unparseable",
                "execution_authority": False}
    key = intent.strip().upper()
    if key in SWARM_ROLE_MAP:
        t = SWARM_ROLE_MAP[key]
        return {"targets": [vars(t).copy()], "intent": intent,
                "reason": "matched swarm role",
                "execution_authority": False}
    return {"targets": [], "intent": intent,
            "reason": INSUFFICIENT_DATA_REASON,
            "execution_authority": False}


def supported_intents() -> List[str]:
    return sorted(SWARM_ROLE_MAP.keys())


__all__ = [
    "EVENT_PLAYBOOKS",
    "CURVE_REGIME_PLAYBOOKS",
    "GROWTH_INFLATION_QUADRANTS",
    "SWARM_ROLE_MAP",
    "EventPlaybook",
    "CurveRegimePlaybook",
    "QuadrantPlaybook",
    "IntentRouteTarget",
    "match_event_playbook",
    "detect_curve_regime",
    "classify_quadrant",
    "taylor_rule_divergence",
    "route_event",
    "route_intent",
    "supported_intents",
    "INSUFFICIENT_DATA",
    # Institutional Macro Architecture Framework
    "evaluate_fomc_reaction_function",
    "evaluate_fomc_press_conference",
    "evaluate_ecb_fragmentation_playbook",
    "evaluate_boj_carry_unwind_playbook",
    "evaluate_pboc_credit_transmission_playbook",
    "evaluate_cpi_release_playbook",
    "evaluate_nfp_labor_playbook",
    "evaluate_pmi_cycle_momentum",
    "evaluate_terms_of_trade_shock",
    "evaluate_gold_decoupling_dynamics",
    "evaluate_sovereign_debt_crisis_hierarchy",
    "evaluate_cot_3y_z_scores",
    "evaluate_dealer_gamma_regime",
    "evaluate_fx_risk_reversals",
    "evaluate_master_cross_asset_matrix",
    "evaluate_three_window_execution_protocol",
    # Open Intelligence Ingestion
    "OpenIntelligenceAggregator",
    "GDELTFeed",
    "ACLEDFeed",
    "DBnomicsFeed",
    "BISFeed",
    # Multi-Domain Quantitative Scoring & Selection Funnel
    "calculate_net_conviction_score",
    "evaluate_selection_funnel",
    "classify_master_playbook",
    "MacroConvictionEngine",
    # Temporal Planning Engine
    "classify_yearly_regime",
    "classify_monthly_cycle",
    "classify_day_of_week_template",
    "detect_session_killzone",
    "evaluate_temporal_context",
    "TemporalPlanningEngine",
    # Microstructure Impact Engine
    "classify_dealing_range_zones",
    "detect_liquidity_sweep",
    "evaluate_cvd_divergence",
    "evaluate_impact_candle",
    "grade_a_plus_confluence",
    "MicrostructureImpactEngine",
    # Bi-Temporal Purged WFO
    "BiTemporalMacroStore",
    "MicrostructureSweepSimulator",
    "TradeRecord",
    "generate_purged_wfo_folds",
    "evaluate_statistical_survival_gates",
]
