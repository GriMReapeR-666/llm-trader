"""THX-01 Decision Advisor — in-process calibrated decision layer.

THX-01 (doofz/THX-01) is a 322M-param non-autoregressive model that evaluates
a market-context string and returns calibrated probability answers for structured
questions in a single ~10 ms forward pass.

This module is a lazy singleton: the model loads on first call, then stays
resident in memory. All decision output is advisory only; execution_authority
is never granted here.

Usage (from signal_arbitrator or graph_nodes):
    from intelligence.thx01_advisor import advise_market

    signal = advise_market(
        context_text=...,   # free-text summary of the current market state
        features=...,       # feature dict from build_feature_snapshot
    )
    # Returns dict matching the signal shape expected by _extract_signals_from_state
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

log = logging.getLogger("THX01Advisor")

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_agent = None          # thx01 agent, loaded on first use
_load_error: Optional[str] = None   # populated if import or load fails

_MODEL_ID = "doofz/THX-01"

# ---------------------------------------------------------------------------
# Question schema — the 6 questions asked every cycle
# ---------------------------------------------------------------------------
_QUESTIONS = {
    "direction": {
        "type": "choice",
        "question": "What is the most likely near-term market direction for this asset?",
        "criteria": {
            "BUY": "Price is expected to rise; bullish momentum or macro tailwind",
            "SELL": "Price is expected to fall; bearish momentum or macro headwind",
            "HOLD": "No clear directional edge; conditions are neutral or conflicted",
        },
    },
    "high_conviction": {
        "type": "noul",
        "question": (
            "Is this a high-conviction trading setup? "
            "Respond yes only if multiple independent signals (technical, macro, causal) "
            "agree and risk/reward is clearly favourable."
        ),
    },
    "risk_level": {
        "type": "score",
        "question": "What is the current market risk level for taking a new position?",
        "criteria": ["LOW", "MEDIUM", "HIGH", "EXTREME"],
    },
    "vix_elevated": {
        "type": "noul",
        "question": (
            "Is market volatility or fear elevated? "
            "Respond yes if VIX is above 25, implied volatility is rising, "
            "or there are signs of systemic stress."
        ),
    },
    "regime": {
        "type": "choice",
        "question": "What macro regime best describes current market conditions?",
        "criteria": {
            "RISK_ON": "Risk appetite is strong; equities and high-yield assets leading",
            "RISK_OFF": "Defensive positioning; flight to safety assets dominating",
            "NEUTRAL": "Transition or mixed signals; no dominant regime theme",
            "CRISIS": "Systemic stress; disorderly markets or multi-asset correlation breakdown",
        },
    },
    "stop_distance": {
        "type": "number",
        "question": (
            "What is the numeric stop-loss distance or risk amount stated in the context, "
            "expressed in price points or pips? Return null if not stated."
        ),
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_agent():
    """Load THX-01 once, cache globally. Thread-safe.

    Uses a project-local cache dir to avoid the Windows symlink privilege
    error (WinError 1314) that occurs when HuggingFace tries to create
    symlinks in the default ~/.cache path on accounts without Developer Mode.
    """
    global _agent, _load_error
    if _agent is not None or _load_error is not None:
        return
    with _lock:
        if _agent is not None or _load_error is not None:
            return
        try:
            import os
            import pathlib
            import thx01  # noqa: PLC0415

            # Point HF cache to project-local models/thx01 — plain directory,
            # no symlinks needed, works on any Windows account.
            _cache = str(
                pathlib.Path(__file__).resolve().parents[1] / "models" / "thx01-cache"
            )
            pathlib.Path(_cache).mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("HF_HUB_CACHE", _cache)
            # Suppress the symlink warning — we're using a flat cache.
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

            log.info("THX01Advisor: loading %s → cache=%s", _MODEL_ID, _cache)
            _agent = thx01.load(_MODEL_ID)   # GPU if available, else CPU
            log.info("THX01Advisor: model ready — %s", _MODEL_ID)
        except Exception as exc:
            _load_error = str(exc)
            log.warning("THX01Advisor: failed to load model — %s", exc)


def _build_context(context_text: str, features: Dict[str, Any]) -> str:
    """Compose the state string THX-01 will read.

    Combines the free-text market context with key numeric features so the model
    can answer number/noul questions with grounding.
    """
    lines = [context_text.strip()] if context_text else []

    # Append a compact feature summary so number questions have grounding
    numeric_parts = []
    for key in ("vix", "rsi_14", "adx_14", "atr_14", "spread_pips",
                 "stop_distance_pips", "risk_reward_ratio"):
        val = features.get(key)
        if val is not None:
            numeric_parts.append(f"{key}={val}")

    # Regime / macro tags
    for key in ("regime", "macro_regime", "session", "trend_direction",
                 "market_condition", "event_blackout"):
        val = features.get(key)
        if val is not None:
            numeric_parts.append(f"{key}={val}")

    if numeric_parts:
        lines.append("Feature snapshot: " + ", ".join(numeric_parts) + ".")

    return " ".join(lines) or "No market context available."


def _parse_result(result: Dict[str, Any], features: Dict[str, Any]) -> Dict[str, Any]:
    """Convert thx01 answer dict into the signal shape used by signal_arbitrator."""
    if not isinstance(result, dict):
        answers = {}
    elif "answers" in result and isinstance(result["answers"], dict):
        answers = result["answers"]
    else:
        answers = result

    # --- Direction ---
    dir_ans = answers.get("direction", {})
    direction = str(dir_ans.get("choice") or dir_ans.get("selected_option") or "HOLD").upper()
    dir_probs = dir_ans.get("probabilities") or {}
    dir_prob = float(dir_probs.get(direction) or dir_ans.get("confidence") or 0.5)

    if direction not in ("BUY", "SELL"):
        # THX-01 chose HOLD — return no-signal marker
        return {
            "source": "THX01",
            "direction": "HOLD",
            "confidence": 0.0,
            "entry_price": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "setup_name": "thx01_hold",
            "rationale": "THX-01: HOLD selected",
            "risk_fraction": 0.0,
            "thx01_answers": answers,
        }

    # --- Conviction ---
    conv_ans = answers.get("high_conviction", {})
    conviction_prob = float(conv_ans.get("noul") or conv_ans.get("p_yes") or conv_ans.get("confidence") or 0.5)

    # --- Risk level ---
    risk_ans = answers.get("risk_level", {})
    risk_level = "MEDIUM"
    legend = risk_ans.get("legend") or {}
    probs = risk_ans.get("probabilities") or {}
    if probs and legend:
        best_idx = max(probs.keys(), key=lambda k: float(probs[k]))
        risk_level = str(legend.get(best_idx) or "MEDIUM").upper()
    elif "score" in risk_ans:
        score_val = round(float(risk_ans["score"]))
        risk_level = str(legend.get(str(score_val)) or "MEDIUM").upper()
    else:
        risk_level = str(risk_ans.get("choice") or risk_ans.get("selected_option") or "MEDIUM").upper()

    risk_discount = {"LOW": 1.0, "MEDIUM": 0.85, "HIGH": 0.60, "EXTREME": 0.20}.get(risk_level, 0.85)

    # --- VIX flag ---
    vix_ans = answers.get("vix_elevated", {})
    vix_p = float(vix_ans.get("noul") or vix_ans.get("p_yes") or vix_ans.get("confidence") or 0.0)
    vix_elevated = vix_p > 0.6

    # --- Regime ---
    reg_ans = answers.get("regime", {})
    thx01_regime = str(reg_ans.get("choice") or reg_ans.get("selected_option") or "NEUTRAL").upper()

    # --- Stop distance (number extraction) ---
    stop_ans = answers.get("stop_distance", {})
    raw_stop = stop_ans.get("value")
    extracted_stop = float(raw_stop) if raw_stop is not None else 0.0

    # Composite confidence: direction probability × conviction × risk discount
    confidence = dir_prob * conviction_prob * risk_discount
    # Cap to [0.01, 0.98] — never fully certain
    confidence = max(0.01, min(0.98, confidence))

    # Apply VIX penalty
    if vix_elevated:
        confidence *= 0.75

    rationale_parts = [
        f"THX-01: dir={direction}(p={dir_prob:.2f})",
        f"conviction={conviction_prob:.2f}",
        f"risk={risk_level}",
        f"regime={thx01_regime}",
    ]
    if vix_elevated:
        rationale_parts.append("vix_elevated=True → confidence×0.75")
    if extracted_stop > 0:
        rationale_parts.append(f"extracted_stop={extracted_stop}")

    return {
        "source": "THX01",
        "direction": direction,
        "confidence": round(confidence, 4),
        "entry_price": 0.0,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "setup_name": "thx01_calibrated",
        "rationale": " | ".join(rationale_parts),
        "risk_fraction": 0.01,
        "thx01_regime": thx01_regime,
        "thx01_risk_level": risk_level,
        "thx01_vix_elevated": vix_elevated,
        "thx01_answers": answers,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def advise_market(
    context_text: str = "",
    features: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Run THX-01 over the current market context and return a signal dict.

    Returns None if the model is unavailable or raises; callers must handle
    gracefully (signal_arbitrator skips None entries).

    The returned dict matches the signal shape in _extract_signals_from_state:
      source, direction, confidence, entry_price, stop_loss, take_profit,
      setup_name, rationale, risk_fraction.
    """
    _load_agent()

    if _agent is None:
        if _load_error:
            log.debug("THX01Advisor: skipping — %s", _load_error)
        return None

    features = features or {}
    state_text = _build_context(context_text, features)

    try:
        raw = _agent.decide(state_text, _QUESTIONS)
        return _parse_result(raw, features)
    except Exception as exc:
        log.warning("THX01Advisor: inference error — %s", exc)
        return None


def is_available() -> bool:
    """Return True if the THX-01 model loaded successfully."""
    _load_agent()
    return _agent is not None


def model_id() -> str:
    return _MODEL_ID
