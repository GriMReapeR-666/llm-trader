"""Bridge discrete RL actions to continuous execution parameters.

RL Q-learning outputs only BUY / SELL / HOLD. Live execution needs entry,
stop-loss, take-profit, lot sizing inputs, strategy, and magic numbers.
This module produces advisory continuous parameters from market context.

``execution_authority`` is always False — bridged params never bypass
DeterministicValidator / PortfolioRiskArbiter.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from trading_safety import SCALP_MAGIC, SWING_MAGIC

DISCRETE_ACTIONS = frozenset({"BUY", "SELL", "HOLD"})


@dataclass(frozen=True)
class BridgedExecutionParams:
    action: str
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_fraction: Optional[float] = None
    strategy: str = "scalp"
    magic: int = SCALP_MAGIC
    atr: Optional[float] = None
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    rationale: str = ""
    source: str = "rl_action_bridge"
    execution_authority: bool = False
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, payload: Optional[Dict[str, Any]]) -> "BridgedExecutionParams":
        data = dict(payload or {})
        action = str(data.get("action") or "HOLD").upper()
        strategy = str(data.get("strategy") or "scalp")
        try:
            magic = int(data.get("magic") or 0)
        except (TypeError, ValueError):
            magic = 0
        if magic <= 0:
            magic = SWING_MAGIC if strategy == "swing" else SCALP_MAGIC
        return cls(
            action=action if action in DISCRETE_ACTIONS else "HOLD",
            entry=_finite_positive(data.get("entry")),
            stop_loss=_finite_positive(data.get("stop_loss")),
            take_profit=_finite_positive(data.get("take_profit")),
            risk_fraction=_finite_positive(data.get("risk_fraction")),
            strategy=strategy,
            magic=magic,
            atr=_finite_positive(data.get("atr")),
            atr_sl_mult=float(data.get("atr_sl_mult") or 1.5),
            atr_tp_mult=float(data.get("atr_tp_mult") or 3.0),
            rationale=str(data.get("rationale") or ""),
            source=str(data.get("source") or "rl_action_bridge"),
            execution_authority=False,
            extras=dict(data.get("extras") or {}),
        )

    def to_decision_overlay(self) -> Dict[str, Any]:
        """Fields that can fill gaps in an incomplete LLM decision."""
        if self.action == "HOLD":
            return {"action": "HOLD", "source": self.source, "execution_authority": False}
        return {
            "action": self.action,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "position_size": None,  # risk manager owns sizing
            "strategy_hint": self.strategy,
            "magic": self.magic,
            "source": self.source,
            "execution_authority": False,
            "rl_bridge": {
                "atr": self.atr,
                "atr_sl_mult": self.atr_sl_mult,
                "atr_tp_mult": self.atr_tp_mult,
                "risk_fraction": self.risk_fraction,
                "rationale": self.rationale,
            },
        }


def _finite_positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _infer_strategy(toon: Dict[str, Any], atr: Optional[float], price: Optional[float]) -> str:
    timeframe = str(toon.get("timeframe") or toon.get("strategy") or "").lower()
    if "swing" in timeframe or "h4" in timeframe or "d1" in timeframe:
        return "swing"
    if atr and price and atr / price >= 0.008:
        return "swing"
    session = str(
        (toon.get("rl_suggestion") or {}).get("session")
        or toon.get("session")
        or ""
    ).lower()
    if session in {"asia", "overnight"}:
        return "swing"
    return "scalp"


def bridge_rl_action(
    action: str,
    *,
    toon: Optional[Dict[str, Any]] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    atr: Optional[float] = None,
    risk_fraction: float = 0.01,
    atr_sl_mult: float = 1.5,
    atr_tp_mult: float = 3.0,
) -> BridgedExecutionParams:
    """Map a discrete RL action into continuous advisory execution params."""
    toon = toon or {}
    discrete = str(action or "HOLD").upper().strip()
    if discrete not in DISCRETE_ACTIONS:
        return BridgedExecutionParams(
            action="HOLD",
            rationale=f"unsupported RL action {action!r}; forced HOLD",
        )
    if discrete == "HOLD":
        return BridgedExecutionParams(action="HOLD", rationale="RL selected HOLD")

    dom = toon.get("dom") or {}
    bid_v = _finite_positive(bid if bid is not None else dom.get("best_bid") or toon.get("bid"))
    ask_v = _finite_positive(ask if ask is not None else dom.get("best_ask") or toon.get("ask"))
    mid = None
    if bid_v and ask_v and ask_v >= bid_v:
        mid = (bid_v + ask_v) / 2.0
    elif bid_v:
        mid = bid_v
    elif ask_v:
        mid = ask_v
    else:
        ohlc = toon.get("ohlc") or []
        if isinstance(ohlc, list) and ohlc:
            mid = _finite_positive((ohlc[-1] or {}).get("close"))

    indicators = toon.get("indicators") or {}
    atr_v = _finite_positive(atr)
    if atr_v is None:
        raw_atr = indicators.get("atr_14")
        if isinstance(raw_atr, (list, tuple)) and raw_atr:
            raw_atr = raw_atr[-1]
        atr_v = _finite_positive(raw_atr)
    if atr_v is None and mid is not None:
        atr_v = mid * 0.001  # last-resort scale; still advisory only

    if mid is None or atr_v is None:
        return BridgedExecutionParams(
            action="HOLD",
            rationale="cannot bridge RL action without price/ATR context",
            atr=atr_v,
        )

    entry = ask_v if discrete == "BUY" and ask_v else (bid_v if discrete == "SELL" and bid_v else mid)
    direction = 1.0 if discrete == "BUY" else -1.0
    stop_loss = entry - direction * atr_v * float(atr_sl_mult)
    take_profit = entry + direction * atr_v * float(atr_tp_mult)
    if stop_loss <= 0 or take_profit <= 0:
        return BridgedExecutionParams(
            action="HOLD",
            rationale="bridged SL/TP non-positive; forced HOLD",
            atr=atr_v,
        )

    strategy = _infer_strategy(toon, atr_v, mid)
    magic = SWING_MAGIC if strategy == "swing" else SCALP_MAGIC
    try:
        risk_frac = float(risk_fraction)
        if not math.isfinite(risk_frac) or risk_frac <= 0:
            risk_frac = 0.01
    except (TypeError, ValueError, OverflowError):
        risk_frac = 0.01

    return BridgedExecutionParams(
        action=discrete,
        entry=round(float(entry), 5),
        stop_loss=round(float(stop_loss), 5),
        take_profit=round(float(take_profit), 5),
        risk_fraction=risk_frac,
        strategy=strategy,
        magic=magic,
        atr=round(float(atr_v), 8),
        atr_sl_mult=float(atr_sl_mult),
        atr_tp_mult=float(atr_tp_mult),
        rationale=f"RL {discrete} bridged via ATR x{atr_sl_mult}/{atr_tp_mult}",
        extras={"mid": round(float(mid), 5), "bid": bid_v, "ask": ask_v},
    )


def merge_bridge_into_decision(
    decision: Dict[str, Any],
    bridged: BridgedExecutionParams,
    *,
    fill_missing_only: bool = True,
) -> Dict[str, Any]:
    """Overlay bridged continuous numeric params onto an LLM decision without granting authority.

    The RL bridge may fill numeric fields only (entry, stop_loss, take_profit)
    and NEVER mutates or overrides the decision 'action'. A HOLD/NONE stays HOLD/NONE.
    """
    out = dict(decision or {})
    out["rl_bridged_params"] = bridged.as_dict()
    out["execution_authority"] = False
    if bridged.action == "HOLD":
        return out

    overlay = bridged.to_decision_overlay()
    if fill_missing_only:
        # Numeric continuous execution parameters only: action is NEVER changed
        for key in ("entry", "stop_loss", "take_profit"):
            if out.get(key) in (None, "", 0, 0.0) and overlay.get(key) is not None:
                out[key] = overlay[key]
        if not out.get("strategy_hint"):
            out["strategy_hint"] = bridged.strategy
    else:
        out.update({k: v for k, v in overlay.items() if k not in ("action", "execution_authority")})
        out["execution_authority"] = False
    return out
