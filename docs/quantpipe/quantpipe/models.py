"""Broker profiles + market-condition scenarios."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path

ROOT = Path(os.getenv("QP_ROOT", Path(__file__).resolve().parent.parent))
HOME = Path(os.getenv("QP_HOME", ROOT / "qp_data"))
HOME.mkdir(parents=True, exist_ok=True)


@dataclass
class BrokerProfile:
    name: str
    point: float = 0.00001
    digits: int = 5
    contract_size: float = 100000.0
    spread_points: float = 12.0
    commission_per_lot: float = 0.0      # round-turn, account currency
    slippage_points: float = 1.0
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 100.0
    leverage: float = 100.0
    stops_level_points: float = 0.0
    swap_per_lot_day: float = 0.0        # signed (negative = cost)
    stop_out_level: float = 50.0         # margin level %
    usd_base: bool = False               # True for USDJPY-style (quote != account ccy)
    symbols: dict = field(default_factory=dict)

    def for_symbol(self, symbol: str) -> "BrokerProfile":
        ov = self.symbols.get(symbol.upper(), {})
        return replace(self, **ov) if ov else self

    def to_dict(self):
        return asdict(self)


def load_brokers() -> dict[str, BrokerProfile]:
    raw = json.loads((ROOT / "config" / "brokers.json").read_text())
    custom = HOME / "brokers.json"           # user overrides / additions
    if custom.exists():
        raw.update(json.loads(custom.read_text()))
    return {k: BrokerProfile(name=k, **v) for k, v in raw.items()}


@dataclass
class Scenario:
    name: str
    vol_mult: float = 1.0
    spread_mult: float = 1.0
    slippage_mult: float = 1.0
    gap_prob: float = 0.0      # per-bar probability of a news-style jump
    gap_pct: float = 0.0       # jump size as fraction of price
    seed: int = 7

    def to_dict(self):
        return asdict(self)


SCENARIOS = {
    "baseline":    Scenario("baseline"),
    "high_vol":    Scenario("high_vol", vol_mult=1.6, spread_mult=2.0, slippage_mult=2.0),
    "low_vol":     Scenario("low_vol", vol_mult=0.6),
    "wide_spread": Scenario("wide_spread", spread_mult=3.0, slippage_mult=3.0),
    "news_gaps":   Scenario("news_gaps", gap_prob=0.002, gap_pct=0.004),
    "crisis":      Scenario("crisis", vol_mult=2.2, spread_mult=4.0, slippage_mult=4.0,
                            gap_prob=0.004, gap_pct=0.006),
}

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def bars_per_year(tf: str) -> float:
    return 252 * 1440 / TF_MINUTES[tf]
