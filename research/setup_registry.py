"""Versioned deterministic setup classification.

The LLM may describe a setup, but this registry assigns the only setup IDs used
for journal segmentation, duplicate prevention, and future research.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict


@dataclass(frozen=True)
class SetupSpec:
    setup_id: str
    version: str
    description: str
    min_rr: float
    allowed_sessions: tuple[str, ...]
    trigger: str
    invalidation: str
    stop_style: str
    target_style: str
    expected_holding: str
    event_exclusion: bool


SETUPS = {
    "trend_pullback_v1": SetupSpec("trend_pullback_v1", "1", "Multi-timeframe trend-aligned continuation near a key level.", 2.0, ("LONDON", "NY-LONDON", "NEW YORK"), "completed pullback confirmation", "close through structural pullback level", "beyond pullback swing", "next structural level / >=2R", "minutes-to-hours", True),
    "range_reversal_v1": SetupSpec("range_reversal_v1", "1", "Range-bound reversal at a key level with confirmation.", 2.0, ("ASIAN", "LONDON", "NEW YORK"), "completed reversal candle at range edge", "completed close beyond range edge", "beyond rejection wick", "range midpoint/opposite edge / >=2R", "minutes-to-hours", True),
    "liquidity_sweep_reversal_v1": SetupSpec("liquidity_sweep_reversal_v1", "1", "Sweep/rejection reversal with completed-candle and volume confirmation.", 2.0, ("LONDON", "NY-LONDON", "NEW YORK"), "sweep + wick rejection + volume-confirmed close", "close back through swept level", "beyond sweep extreme", "opposite liquidity pool / >=2R", "minutes-to-hours", True),
    "asian_range_breakout_v1": SetupSpec("asian_range_breakout_v1", "1", "London/NY breakout of a defined Asian range.", 2.0, ("LONDON", "NY-LONDON"), "completed breakout close outside Asian range", "close back inside range", "inside range behind breakout", "measured range extension / >=2R", "minutes-to-hours", True),
    "macro_continuation_v1": SetupSpec("macro_continuation_v1", "1", "Post-event continuation only after the event blackout and confirmed structure.", 2.5, ("LONDON", "NY-LONDON", "NEW YORK"), "post-event completed continuation candle", "close through event reaction structure", "beyond event reaction swing", "next macro/structural level / >=2.5R", "hours-to-days", False),
    # Fixed 2R bracket continuation. NOTE: the 150 journal `scalp` trades are
    # NOT evidence for this geometry - they are synthetic fixture rows (a single
    # distinct entry/stop/TP of 1.1/1.0/1.3, realised R only ever -1.0 or +1.5,
    # zero trades reaching 2.0R) that are labelled source="broker". So this spec
    # encodes a target geometry, NOT a measured win rate, and must earn its own
    # evidence from real fills. It is a FALLBACK for signals no structural setup
    # matches, and must never outrank the five above.
    "scalp_bracket_v1": SetupSpec(
        "scalp_bracket_v1", "1",
        "Fixed 2R bracket continuation for engine-mode bracket signals.",
        2.0, ("ASIAN", "LONDON", "NY-LONDON", "NEW YORK"),
        "fixed 2R bracket entry", "bracket stop", "beyond bracket stop",
        "fixed +2R target", "hours-to-days", True),
    "momentum_cascade_v1": SetupSpec(
        "momentum_cascade_v1", "1",
        "Hawkes self-exciting momentum cascade with volume and VPIN toxicity confirmation.",
        2.0, ("LONDON", "NY-LONDON", "NEW YORK"),
        "hawkes branching ratio >= 0.85 + volume surge + confirmed bar close",
        "close back through initiation bar or adverse VPIN spike >= 0.70",
        "beyond cascade initiation swing",
        "next liquidity pool / >=2R",
        "minutes-to-hours", True),
}


def _unmet_conditions(adx: float, aligned: bool, near_level: bool, confirmed: bool,
                      volume: bool, sweep: bool, asian_breakout: bool,
                      macro_continuation: bool) -> Dict[str, list]:
    """Per-setup conditions that were not satisfied, for rejection reporting.

    Observability only — no gate is relaxed. Lets a rare setup be told apart
    from an unreachable one.
    """
    checks = {
        "macro_continuation_v1": (aligned, confirmed, volume),
        "asian_range_breakout_v1": (asian_breakout, confirmed, volume),
        "liquidity_sweep_reversal_v1": (sweep, near_level, confirmed, volume),
        "trend_pullback_v1": (adx >= 20, aligned, near_level, confirmed),
        "range_reversal_v1": (adx < 20, near_level, confirmed, volume),
    }
    names = ("trigger", "ema/mtf_alignment", "near_key_level",
             "confirmed_candle", "volume_confirmed")
    unmet: Dict[str, list] = {}
    for setup_id, conditions in checks.items():
        missing = [name for name, ok in zip(names, conditions) if not ok]
        if missing:
            unmet[setup_id] = missing
    return unmet


def classify(features: Dict[str, Any], session: str = "",
            strategy: str = "") -> Dict[str, Any]:
    """Return a stable setup ID from independently calculated evidence."""
    if not isinstance(features, dict):
        return {"setup_id": "unclassified_v1", "eligible": False,
                "reason": "malformed feature payload"}
    normalized_session = str(session or "").upper()
    raw_adx = features.get("adx") if features.get("adx") is not None else features.get("adx_14", 0)
    try:
        adx = float(raw_adx or 0)
    except (TypeError, ValueError):
        return {"setup_id": "unclassified_v1", "eligible": False,
                "reason": "malformed ADX feature"}
    if not math.isfinite(adx) or adx < 0:
        return {"setup_id": "unclassified_v1", "eligible": False,
                "reason": "invalid ADX feature"}

    # Validate remaining scalar numeric features for non-finite values (NaN / Inf)
    for key, val in features.items():
        if key not in ("adx", "adx_14") and isinstance(val, (int, float)) and not isinstance(val, bool):
            if not math.isfinite(float(val)):
                return {"setup_id": "unclassified_v1", "eligible": False,
                        "reason": f"non-finite feature value for {key}"}

    aligned = features.get("ema_alignment") == features.get("mtf_alignment") and features.get("mtf_alignment") in (-1, 1)
    near_level = bool(features.get("near_key_level"))
    confirmed = bool(features.get("confirmed_candle") if "confirmed_candle" in features else (features.get("completed_bar_time") is not None or "close" in features))
    volume = bool(features.get("volume_confirmed") if "volume_confirmed" in features else (float(features.get("tick_volume_ratio_20", 0) or 0) >= 1.1))
    sweep = bool(features.get("liquidity_sweep"))
    asian_breakout = bool(features.get("asian_range_breakout"))
    macro_continuation = bool(features.get("macro_continuation"))
    hawkes_cascade = bool(features.get("hawkes_cascade_active"))
    vpin = float(features.get("vpin_toxicity", 0.0) or 0.0)
    vpin_safe = vpin < 0.70
    fallback = False
    if macro_continuation and aligned and confirmed and volume:
        setup_id = "macro_continuation_v1"
    elif hawkes_cascade and adx >= 25 and confirmed and volume and vpin_safe:
        setup_id = "momentum_cascade_v1"
    elif asian_breakout and confirmed and volume:
        setup_id = "asian_range_breakout_v1"
    elif sweep and near_level and confirmed and volume:
        setup_id = "liquidity_sweep_reversal_v1"
    elif adx >= 20 and aligned and near_level and confirmed:
        setup_id = "trend_pullback_v1"
    elif adx < 20 and near_level and confirmed and volume:
        setup_id = "range_reversal_v1"
    else:
        # Name the features this classifier depends on that were never supplied.
        # A trigger feature with no producer (e.g. macro_continuation) makes its
        # setup permanently unreachable, and a bare "no match" hides that.
        required = ("adx", "ema_alignment", "mtf_alignment", "near_key_level",
                    "confirmed_candle", "volume_confirmed", "liquidity_sweep",
                    "asian_range_breakout", "macro_continuation")
        absent = [k for k in required if k not in features]
        # A supplied-but-false feature is a different problem from an absent
        # one, and "no match" conflates them. Report which conditions each
        # setup needed and which were unmet, so a setup that is simply rare
        # (e.g. liquidity_sweep_reversal_v1, whose two-bar reversal plus
        # volume conjunction fires on ~2% of bars) is distinguishable from
        # one that is unreachable. This is observability only: it never
        # relaxes a gate.
        unmet = _unmet_conditions(adx, aligned, near_level, confirmed,
                                  volume, sweep, asian_breakout,
                                  macro_continuation)
        # Engine-mode bracket fallback. `scalp` is an engine mode (trading_safety
        # SCALP_MAGIC), not a structural setup, so without this every scalp signal
        # was unclassifiable and rejected. Only reached when nothing structural
        # matched, so it cannot outrank a real setup.
        if str(strategy or "").strip().lower() == "scalp":
            setup_id = "scalp_bracket_v1"
            fallback = True
            unmet_note = "; unmet structural conditions: %s" % unmet
        else:
            reason = "no registered deterministic setup matched"
            if absent:
                reason += " (features never supplied: %s)" % ", ".join(absent)
            reason += "; unmet structural conditions: %s" % unmet
            return {"setup_id": "unclassified_v1", "eligible": False,
                    "reason": reason, "missing_features": absent,
                    "unmet_conditions": unmet}

    spec = SETUPS[setup_id]
    session_ok = not normalized_session or any(part in normalized_session for part in spec.allowed_sessions)
    if fallback:
        reason = ("structural fallback: engine mode %r matched no registered "
                  "structural setup; using fixed 2R bracket geometry" % (strategy,))
        reason += unmet_note
    else:
        reason = ("matched registered setup" if session_ok
                  else "setup not permitted in current session")
    return {"setup_id": setup_id, "eligible": session_ok, "reason": reason,
            "fallback": fallback, "spec": asdict(spec),
            "unmet_conditions": unmet if fallback else {}}
