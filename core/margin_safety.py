"""Deterministic pre-trade margin and concentration checks.

The broker remains authoritative for margin.  This module deliberately makes
only transparent, conservative arithmetic from a fresh broker account snapshot
and ``order_calc_margin`` estimates.  A stop-out *price* cannot be inferred
reliably across instruments, hedged books, or broker-specific margin models,
so the reported liquidation distance is explicitly an account-currency
``stop_out_loss_buffer`` and number of planned-Rs -- never a fabricated price.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class MarginRiskPolicy:
    """Conservative, broker-reviewable defaults rather than sizing advice."""

    max_margin_to_equity: float = 0.35
    min_free_margin_to_equity: float = 0.50
    min_margin_level_percent: float = 300.0
    stop_out_margin_level_buffer_percent: float = 200.0
    min_stop_out_buffer_r: float = 2.0
    max_new_symbol_margin_to_equity: float = 0.20
    max_driver_margin_to_equity: float = 0.25

    def __post_init__(self) -> None:
        bounded = {
            "max_margin_to_equity": self.max_margin_to_equity,
            "min_free_margin_to_equity": self.min_free_margin_to_equity,
            "max_new_symbol_margin_to_equity": self.max_new_symbol_margin_to_equity,
            "max_driver_margin_to_equity": self.max_driver_margin_to_equity,
        }
        for name, value in bounded.items():
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or not 0 <= float(value) <= 1):
                raise ValueError(f"{name} must be finite and within [0, 1]")
        positive = {
            "min_margin_level_percent": self.min_margin_level_percent,
            "stop_out_margin_level_buffer_percent": self.stop_out_margin_level_buffer_percent,
            "min_stop_out_buffer_r": self.min_stop_out_buffer_r,
        }
        for name, value in positive.items():
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or float(value) <= 0):
                raise ValueError(f"{name} must be finite and positive")


@dataclass(frozen=True)
class MarginRiskAssessment:
    approved: bool
    reasons: List[str]
    mode: str
    projected_margin: float | None
    projected_free_margin: float | None
    projected_margin_level_percent: float | None
    stop_out_loss_buffer: float | None
    stop_out_buffer_r: float | None
    new_symbol_margin: float | None
    driver_margin: float | None
    policy: Dict[str, float]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MarginSafetyGate:
    """Fail-closed margin gate used after the broker calculates new margin."""

    def __init__(self, policy: MarginRiskPolicy | None = None):
        self.policy = policy or MarginRiskPolicy()

    @staticmethod
    def _number(values: Dict[str, Any], name: str) -> float | None:
        try:
            value = values.get(name)
            value = None if value is None else float(value)
            return value if value is not None and math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_percent_stop_out(account: Dict[str, Any]) -> bool:
        """MT5 uses percent (0) for ``ACCOUNT_MARGIN_SO_MODE_PERCENT``.

        Accept a readable string too, which lets stored/replayed snapshots be
        evaluated without importing the MetaTrader module.
        """
        value = account.get("margin_so_mode")
        return value == 0 or str(value).upper() in {"0", "PERCENT", "ACCOUNT_MARGIN_SO_MODE_PERCENT"}

    def assess(
        self,
        *,
        account: Dict[str, Any],
        new_margin: float | None,
        new_risk: float,
        symbol: str,
        driver_buckets: Iterable[str],
        position_margin_estimates: Iterable[Dict[str, Any]],
        mode: str = "mt5",
    ) -> MarginRiskAssessment:
        """Assess fresh broker data and return an immutable audit result.

        ``position_margin_estimates`` is a list of conservative *gross* margin
        estimates, with ``symbol``, ``buckets`` and ``margin`` keys.  Gross
        estimates intentionally do not rely on hedge offsets that may vanish
        under broker stress.
        """
        policy = self.policy
        reasons: List[str] = []
        if not isinstance(mode, str) or not mode.strip() or mode != "mt5":
            return MarginRiskAssessment(
                approved=False, reasons=["broker margin safety evidence unavailable outside MT5 mode"],
                mode=mode, projected_margin=None, projected_free_margin=None,
                projected_margin_level_percent=None, stop_out_loss_buffer=None,
                stop_out_buffer_r=None, new_symbol_margin=None, driver_margin=None,
                policy=asdict(policy),
            )
        if not isinstance(account, dict):
            account = {}
            reasons.append("broker account margin snapshot is malformed")
        if not isinstance(symbol, str) or not symbol.strip():
            symbol = ""
            reasons.append("margin assessment symbol is missing or malformed")

        equity = self._number(account, "equity")
        margin = self._number(account, "margin")
        free_margin = self._number(account, "margin_free")
        estimated = self._number({"new_margin": new_margin}, "new_margin")
        risk_value = self._number({"new_risk": new_risk}, "new_risk")
        if risk_value is None or risk_value < 0:
            reasons.append("new-order risk is unavailable or invalid")
        if equity is None or equity <= 0:
            reasons.append("broker equity unavailable or non-positive")
        if margin is None or margin < 0:
            reasons.append("broker current margin unavailable")
        if free_margin is None or free_margin < 0:
            reasons.append("broker free margin unavailable")
        if estimated is None or estimated < 0:
            reasons.append("broker new-order margin unavailable")
        if reasons:
            return MarginRiskAssessment(False, reasons, mode, None, None, None, None, None, None, None, asdict(policy))

        try:
            estimates = list(position_margin_estimates)
        except TypeError:
            estimates = []
            reasons.append("position margin estimates are malformed")
        valid_estimates: List[Dict[str, Any]] = []
        for item in estimates:
            if not isinstance(item, dict):
                reasons.append("position margin estimate is malformed")
                continue
            item_margin = self._number(item, "margin")
            if item_margin is None or item_margin < 0:
                reasons.append("position margin estimate is unavailable or invalid")
                continue
            buckets = item.get("buckets", [])
            if isinstance(buckets, str):
                buckets = [buckets]
            if not isinstance(buckets, (list, tuple, set)):
                reasons.append("position margin estimate buckets are malformed")
                continue
            valid_estimates.append(item)
        position_margin_estimates = valid_estimates

        projected_margin = margin + estimated
        projected_free = free_margin - estimated
        projected_level = (equity / projected_margin * 100.0) if projected_margin > 0 else float("inf")
        if projected_margin > equity * policy.max_margin_to_equity:
            reasons.append("projected margin/equity concentration limit")
        if projected_free < equity * policy.min_free_margin_to_equity:
            reasons.append("projected free-margin buffer limit")

        stop_out_buffer: float | None = None
        stop_out_r: float | None = None
        stop_out = self._number(account, "margin_so_so")
        if not self._is_percent_stop_out(account) or stop_out is None or stop_out < 0:
            reasons.append("unsupported or missing broker stop-out mode/level")
        else:
            required_level = max(policy.min_margin_level_percent, stop_out + policy.stop_out_margin_level_buffer_percent)
            if projected_level < required_level:
                reasons.append("projected margin level below stop-out buffer")
            stop_out_equity = projected_margin * stop_out / 100.0
            stop_out_buffer = equity - stop_out_equity
            total_planned_risk = max(0.0, float(risk_value))
            # Existing open-trade risk is represented by the caller as a
            # synthetic position estimate with ``risk_amount`` when available.
            for item in position_margin_estimates:
                item_risk = self._number(item, "risk_amount")
                if item_risk is None:
                    if item.get("risk_amount") not in (None, ""):
                        reasons.append("position risk estimate is unavailable or invalid")
                    continue
                if item_risk < 0:
                    reasons.append("position risk estimate cannot be negative")
                    continue
                total_planned_risk += item_risk
            stop_out_r = stop_out_buffer / total_planned_risk if total_planned_risk > 0 else None
            if stop_out_buffer <= 0:
                reasons.append("projected account already at/below stop-out equity")
            elif stop_out_r is not None and stop_out_r < policy.min_stop_out_buffer_r:
                reasons.append("stop-out loss buffer too close to planned risk")

        symbol_margin = estimated
        driver_margin = estimated
        if isinstance(driver_buckets, str):
            driver_buckets = [driver_buckets]
        try:
            bucket_set = {str(bucket) for bucket in driver_buckets}
        except TypeError:
            bucket_set = set()
            reasons.append("driver margin buckets are malformed")
        for item in position_margin_estimates:
            item_margin = self._number(item, "margin")
            if item_margin is None or item_margin < 0:
                continue
            if str(item.get("symbol", "")).upper() == symbol.upper():
                symbol_margin += item_margin
            if bucket_set.intersection({str(value) for value in item.get("buckets", [])}):
                driver_margin += item_margin
        if symbol_margin > equity * policy.max_new_symbol_margin_to_equity:
            reasons.append("symbol margin concentration limit")
        if driver_margin > equity * policy.max_driver_margin_to_equity:
            reasons.append("driver-bucket margin concentration limit")

        return MarginRiskAssessment(
            approved=not reasons, reasons=reasons, mode=mode,
            projected_margin=projected_margin, projected_free_margin=projected_free,
            projected_margin_level_percent=projected_level,
            stop_out_loss_buffer=stop_out_buffer, stop_out_buffer_r=stop_out_r,
            new_symbol_margin=symbol_margin, driver_margin=driver_margin,
            policy=asdict(policy),
        )

    def assess_from_mt5(
        self,
        *,
        mt5_module: Any,
        symbol: str,
        side: str,
        volume: float,
        price: float,
        new_risk: float,
        driver_buckets: Iterable[str],
        account_override: Dict[str, Any] | None = None,
        position_margin_estimates: Iterable[Dict[str, Any]] | None = None,
    ) -> MarginRiskAssessment:
        """Query live MT5 ``account_info`` / ``order_calc_margin`` then assess.

        Callers may still pass pre-built estimates for tests/replay, but live
        paths should prefer this method so margin evidence is broker-fresh.
        """
        if mt5_module is None:
            return MarginRiskAssessment(
                approved=False, reasons=["MetaTrader5 module unavailable for live margin query"],
                mode="unavailable", projected_margin=None, projected_free_margin=None,
                projected_margin_level_percent=None, stop_out_loss_buffer=None,
                stop_out_buffer_r=None, new_symbol_margin=None, driver_margin=None,
                policy=asdict(self.policy),
            )
        account = dict(account_override or {})
        if not account:
            info = mt5_module.account_info()
            if info is None:
                return MarginRiskAssessment(
                    approved=False, reasons=["mt5.account_info() returned no snapshot"],
                    mode="mt5", projected_margin=None, projected_free_margin=None,
                    projected_margin_level_percent=None, stop_out_loss_buffer=None,
                    stop_out_buffer_r=None, new_symbol_margin=None, driver_margin=None,
                    policy=asdict(self.policy),
                )
            account = {
                "equity": getattr(info, "equity", None),
                "margin": getattr(info, "margin", None),
                "margin_free": getattr(info, "margin_free", None),
                "margin_level": getattr(info, "margin_level", None),
                "margin_so_mode": getattr(info, "margin_so_mode", None),
                "margin_so_call": getattr(info, "margin_so_call", None),
                "margin_so_so": getattr(info, "margin_so_so", None),
            }
        order_type = (
            getattr(mt5_module, "ORDER_TYPE_BUY", 0)
            if str(side).upper() == "BUY"
            else getattr(mt5_module, "ORDER_TYPE_SELL", 1)
        )
        try:
            calculated = mt5_module.order_calc_margin(order_type, symbol, float(volume), float(price))
        except Exception as exc:  # noqa: BLE001 — broker SDK surfaces many failure types
            return MarginRiskAssessment(
                approved=False, reasons=[f"mt5.order_calc_margin failed: {exc}"[:300]],
                mode="mt5", projected_margin=None, projected_free_margin=None,
                projected_margin_level_percent=None, stop_out_loss_buffer=None,
                stop_out_buffer_r=None, new_symbol_margin=None, driver_margin=None,
                policy=asdict(self.policy),
            )
        if calculated is None:
            return MarginRiskAssessment(
                approved=False, reasons=["mt5.order_calc_margin returned None"],
                mode="mt5", projected_margin=None, projected_free_margin=None,
                projected_margin_level_percent=None, stop_out_loss_buffer=None,
                stop_out_buffer_r=None, new_symbol_margin=None, driver_margin=None,
                policy=asdict(self.policy),
            )
        estimates = list(position_margin_estimates or [])
        return self.assess(
            account=account,
            new_margin=float(calculated),
            new_risk=float(new_risk),
            symbol=symbol,
            driver_buckets=driver_buckets,
            position_margin_estimates=estimates,
            mode="mt5",
        )
