"""Universal Multi-Asset Symbol Calibration & Expected Move Engine.

Provides institutional contract specifications, pip conventions, point sizes,
and multi-timeframe expected move analytics across all asset classes:
- Forex Majors & Crosses (EURUSD, GBPUSD, USDJPY, AUDUSD, EURGBP, etc.)
- Precious Metals (XAUUSD Gold, XAGUSD Silver)
- Energy & Commodities (USOIL WTI Crude, UKOIL Brent)
- Equity Indices (US30 Dow, NAS100 Nasdaq, SPX500 S&P 500, GER40 DAX)
- Cryptocurrencies (BTCUSD Bitcoin, ETHUSD Ethereum)

Calculates:
- Precise pip values, tick increments, and contract multipliers per symbol.
- Multi-timeframe expected moves (Session, Daily, Weekly, Monthly).
- Volatility compression / expansion metrics (ATR percentiles, squeeze ratios).
- Macro catalyst expected move multipliers.

Doctrine:
- execution_authority: strictly False.
- Pure Python stdlib & math.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

EXECUTION_AUTHORITY = False


class AssetClass(str, Enum):
    FOREX_MAJOR = "FOREX_MAJOR"
    FOREX_CROSS = "FOREX_CROSS"
    FOREX_JPY = "FOREX_JPY"
    METALS = "METALS"
    COMMODITIES = "COMMODITIES"
    INDICES = "INDICES"
    CRYPTO = "CRYPTO"
    UNKNOWN = "UNKNOWN"


@dataclass
class SymbolSpec:
    symbol: str
    asset_class: AssetClass
    contract_size: float           # Units per 1.00 standard lot
    point_size: float              # Smallest price quote change (e.g. 0.00001 for EURUSD)
    pip_size: float                # 1 retail pip size in price units (e.g. 0.00010 for EURUSD)
    points_per_pip: float          # Number of broker points per pip (usually 10, or 1 for indices)
    min_lot: float                 # Minimum tradeable volume (typically 0.01 micro lot)
    lot_step: float                # Lot step increment (typically 0.01)
    max_lot: float                 # Maximum tradeable volume
    max_spread_pips: float         # Maximum allowed spread in pips before trade rejection
    pip_value_usd_per_standard_lot: float # Dollar value of 1 pip on 1.00 standard lot
    baseline_daily_atr_pips: float # 30-day baseline daily ATR in pips
    description: str


# Institutional Master Symbol Specifications Catalog
SYMBOL_CATALOG: Dict[str, SymbolSpec] = {
    # Forex Majors (0.00010 = 1 pip = 10 points; $10/pip on 1.00 lot)
    "EURUSD": SymbolSpec("EURUSD", AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 1.8, 10.00, 75.0, "Euro / US Dollar"),
    "GBPUSD": SymbolSpec("GBPUSD", AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.2, 10.00, 95.0, "British Pound / US Dollar"),
    "AUDUSD": SymbolSpec("AUDUSD", AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.0, 10.00, 65.0, "Australian Dollar / US Dollar"),
    "NZDUSD": SymbolSpec("NZDUSD", AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.2, 10.00, 60.0, "New Zealand Dollar / US Dollar"),
    "USDCAD": SymbolSpec("USDCAD", AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.2, 7.50, 70.0, "US Dollar / Canadian Dollar"),
    "USDCHF": SymbolSpec("USDCHF", AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.2, 11.20, 65.0, "US Dollar / Swiss Franc"),

    # Forex JPY Pairs (0.010 = 1 pip = 10 points; ~$6.50 - $8.50/pip per lot)
    "USDJPY": SymbolSpec("USDJPY", AssetClass.FOREX_JPY, 100000.0, 0.001, 0.010, 10.0, 0.01, 0.01, 100.0, 2.0, 6.70, 90.0, "US Dollar / Japanese Yen"),
    "EURJPY": SymbolSpec("EURJPY", AssetClass.FOREX_JPY, 100000.0, 0.001, 0.010, 10.0, 0.01, 0.01, 100.0, 2.5, 6.70, 110.0, "Euro / Japanese Yen"),
    "GBPJPY": SymbolSpec("GBPJPY", AssetClass.FOREX_JPY, 100000.0, 0.001, 0.010, 10.0, 0.01, 0.01, 100.0, 3.0, 6.70, 140.0, "British Pound / Japanese Yen"),

    # Forex Crosses
    "EURGBP": SymbolSpec("EURGBP", AssetClass.FOREX_CROSS, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.0, 13.00, 45.0, "Euro / British Pound"),
    "EURAUD": SymbolSpec("EURAUD", AssetClass.FOREX_CROSS, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 3.0, 6.50, 120.0, "Euro / Australian Dollar"),

    # Precious Metals (Convention B: $0.10 move = 1 pip = 10 points; 0.01 lot = 1 oz = $0.10/pip)
    "XAUUSD": SymbolSpec("XAUUSD", AssetClass.METALS, 100.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 2.5, 10.00, 450.0, "Spot Gold (100 oz/lot, $0.10/pip)"),
    "GOLD":   SymbolSpec("GOLD", AssetClass.METALS, 100.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 2.5, 10.00, 450.0, "Spot Gold CFD"),
    "XAGUSD": SymbolSpec("XAGUSD", AssetClass.METALS, 5000.0, 0.001, 0.010, 10.0, 0.01, 0.01, 20.0, 4.0, 50.00, 120.0, "Spot Silver (5,000 oz/lot)"),

    # Commodities & Energy (1 lot = 1,000 bbl; $0.10 = 1 pip; $100/point = $10/pip per lot)
    "USOIL": SymbolSpec("USOIL", AssetClass.COMMODITIES, 1000.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 5.0, 100.00, 180.0, "WTI Crude Oil (1,000 bbl/lot)"),
    "WTI":   SymbolSpec("WTI", AssetClass.COMMODITIES, 1000.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 5.0, 100.00, 180.0, "WTI Crude Oil"),
    "UKOIL": SymbolSpec("UKOIL", AssetClass.COMMODITIES, 1000.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 5.0, 100.00, 190.0, "Brent Crude Oil"),

    # Equity Indices (1 point = 1 pip; point_size = 1.0 or 0.1)
    "US30":   SymbolSpec("US30", AssetClass.INDICES, 1.0, 1.0, 1.0, 1.0, 0.01, 0.01, 50.0, 5.0, 1.00, 380.0, "Dow Jones Industrial Average CFD"),
    "NAS100": SymbolSpec("NAS100", AssetClass.INDICES, 1.0, 0.1, 1.0, 10.0, 0.01, 0.01, 50.0, 3.0, 1.00, 240.0, "Nasdaq 100 CFD"),
    "SPX500": SymbolSpec("SPX500", AssetClass.INDICES, 1.0, 0.1, 1.0, 10.0, 0.01, 0.01, 50.0, 1.5, 1.00, 55.0, "S&P 500 CFD"),
    "GER40":  SymbolSpec("GER40", AssetClass.INDICES, 1.0, 0.1, 1.0, 10.0, 0.01, 0.01, 50.0, 3.0, 1.10, 180.0, "German DAX 40 CFD"),

    # Cryptocurrencies
    "BTCUSD": SymbolSpec("BTCUSD", AssetClass.CRYPTO, 1.0, 0.01, 1.0, 100.0, 0.01, 0.01, 10.0, 50.0, 1.00, 2200.0, "Bitcoin / US Dollar"),
    "ETHUSD": SymbolSpec("ETHUSD", AssetClass.CRYPTO, 1.0, 0.01, 1.0, 100.0, 0.01, 0.01, 50.0, 8.0, 1.00, 150.0, "Ethereum / US Dollar"),
}


@dataclass
class ExpectedMoveResult:
    """Comprehensive expected move analysis for a symbol."""
    symbol: str
    asset_class: str
    current_price: float
    point_size: float
    pip_size: float
    pip_value_usd_per_micro_lot: float

    # Volatility and Expected Moves in Retail Pips
    daily_atr_pips: float
    h4_atr_pips: float
    m15_atr_pips: float
    session_expected_range_pips: float
    daily_expected_range_pips: float
    weekly_expected_range_pips: float
    trade_target_expected_pips: float

    # Market Energy & Compression Diagnostics
    volatility_percentile: float     # 0 to 100 vs 30-day baseline
    is_compressed: bool             # Volatility compression active (energy building)
    compression_ratio: float        # current_daily_atr / baseline_atr
    macro_catalyst_multiplier: float # Multiplier applied due to pending economic catalysts
    recommendation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": "READY_FOR_REVIEW",
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "current_price": round(self.current_price, 5),
            "point_size": self.point_size,
            "pip_size": self.pip_size,
            "pip_value_usd_per_micro_lot": round(self.pip_value_usd_per_micro_lot, 4),
            "daily_atr_pips": round(self.daily_atr_pips, 1),
            "h4_atr_pips": round(self.h4_atr_pips, 1),
            "m15_atr_pips": round(self.m15_atr_pips, 1),
            "session_expected_range_pips": round(self.session_expected_range_pips, 1),
            "daily_expected_range_pips": round(self.daily_expected_range_pips, 1),
            "weekly_expected_range_pips": round(self.weekly_expected_range_pips, 1),
            "trade_target_expected_pips": round(self.trade_target_expected_pips, 1),
            "volatility_percentile": round(self.volatility_percentile, 1),
            "is_compressed": self.is_compressed,
            "compression_ratio": round(self.compression_ratio, 3),
            "macro_catalyst_multiplier": round(self.macro_catalyst_multiplier, 2),
            "recommendation": self.recommendation,
            "execution_authority": False,
        }


class SymbolCalibrationEngine:
    """Universal Calibration & Expected Move Engine."""

    @classmethod
    def get_spec(cls, symbol: str) -> SymbolSpec:
        """Resolves specification for any symbol with automatic inference."""
        clean = (symbol or "").upper().replace("/", "").replace(".", "").replace("-", "")

        # Exact match in catalog
        if clean in SYMBOL_CATALOG:
            return SYMBOL_CATALOG[clean]

        # Fuzzy prefix/suffix matches
        for sym_key, spec in SYMBOL_CATALOG.items():
            if sym_key in clean:
                return spec

        # Fallback heuristic based on symbol patterns
        if "JPY" in clean:
            return SymbolSpec(clean, AssetClass.FOREX_JPY, 100000.0, 0.001, 0.010, 10.0, 0.01, 0.01, 100.0, 2.5, 6.70, 90.0, f"Inferred JPY Pair: {clean}")
        elif any(c in clean for c in ("BTC", "ETH", "SOL", "XRP")):
            return SymbolSpec(clean, AssetClass.CRYPTO, 1.0, 0.01, 1.0, 100.0, 0.01, 0.01, 10.0, 20.0, 1.00, 500.0, f"Inferred Crypto: {clean}")
        elif any(c in clean for c in ("30", "100", "500", "DAX", "FTSE")):
            return SymbolSpec(clean, AssetClass.INDICES, 1.0, 0.1, 1.0, 10.0, 0.01, 0.01, 50.0, 3.0, 1.00, 150.0, f"Inferred Index: {clean}")
        elif any(c in clean for c in ("OIL", "BRENT", "WTI", "GAS")):
            return SymbolSpec(clean, AssetClass.COMMODITIES, 1000.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 5.0, 100.00, 180.0, f"Inferred Commodity: {clean}")
        elif any(c in clean for c in ("XAU", "GOLD", "XAG", "SILV")):
            return SymbolSpec(clean, AssetClass.METALS, 100.0, 0.01, 0.10, 10.0, 0.01, 0.01, 50.0, 2.5, 10.00, 450.0, f"Inferred Metal: {clean}")
        else:
            # Standard Forex Major/Cross fallback
            return SymbolSpec(clean, AssetClass.FOREX_MAJOR, 100000.0, 0.00001, 0.00010, 10.0, 0.01, 0.01, 100.0, 2.0, 10.00, 75.0, f"Inferred Standard Forex: {clean}")

    @classmethod
    def price_to_pips(cls, symbol: str, price_distance: float) -> float:
        """Converts raw price difference into retail pips for the symbol."""
        spec = cls.get_spec(symbol)
        if spec.pip_size <= 0:
            return 0.0
        return abs(float(price_distance)) / spec.pip_size

    @classmethod
    def pips_to_price(cls, symbol: str, pips: float) -> float:
        """Converts retail pips into absolute price distance."""
        spec = cls.get_spec(symbol)
        return abs(float(pips)) * spec.pip_size

    @classmethod
    def calculate_dollar_risk(
        cls,
        symbol: str,
        lot_size: float,
        stop_pips: float,
    ) -> float:
        """Calculates precise dollar risk for given symbol, lot size, and stop pips."""
        spec = cls.get_spec(symbol)
        lots = max(spec.min_lot, float(lot_size))
        pips = max(1.0, float(stop_pips))
        pip_val_micro = spec.pip_value_usd_per_standard_lot * 0.01
        num_micro_lots = lots / 0.01
        return round(num_micro_lots * pip_val_micro * pips, 2)

    @classmethod
    def calculate_expected_move(
        cls,
        symbol: str,
        current_price: float,
        *,
        daily_atr: Optional[float] = None,
        h4_atr: Optional[float] = None,
        m15_atr: Optional[float] = None,
        is_tier1_catalyst_day: bool = False,
        day_of_week_iso: int = 3,  # 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri
        session_name: str = "LONDON_OPEN",
    ) -> ExpectedMoveResult:
        """Calculates institutional expected move pips across multiple timeframes."""
        spec = cls.get_spec(symbol)
        price = max(1e-5, float(current_price or 1.0))

        # Convert ATRs to pips (or default to baseline)
        if daily_atr is not None and daily_atr > 0:
            d_atr_pips = cls.price_to_pips(symbol, daily_atr)
        else:
            d_atr_pips = spec.baseline_daily_atr_pips

        if h4_atr is not None and h4_atr > 0:
            h4_atr_pips = cls.price_to_pips(symbol, h4_atr)
        else:
            h4_atr_pips = d_atr_pips * 0.42

        if m15_atr is not None and m15_atr > 0:
            m15_atr_pips = cls.price_to_pips(symbol, m15_atr)
        else:
            m15_atr_pips = d_atr_pips * 0.12

        # Day of week structural volatility weighting:
        # Mon: 0.85x, Tue: 1.10x, Wed: 1.25x (Peak expansion), Thu: 1.15x, Fri: 0.95x
        day_weights = {1: 0.85, 2: 1.10, 3: 1.25, 4: 1.15, 5: 0.95}
        d_weight = day_weights.get(int(day_of_week_iso), 1.0)

        # Macro catalyst multiplier (FOMC, CPI, NFP expand expected daily range)
        catalyst_mult = 1.65 if is_tier1_catalyst_day else 1.0

        daily_expected_range_pips = d_atr_pips * d_weight * catalyst_mult
        weekly_expected_range_pips = d_atr_pips * 2.85 * (1.25 if is_tier1_catalyst_day else 1.0)

        # Session range share:
        # Asian: 25% of Daily range, London: 55%, NY: 65% (overlaps London)
        session_shares = {
            "ASIAN_ACCUMULATION": 0.28,
            "LONDON_OPEN": 0.58,
            "NY_OPEN": 0.65,
            "OFF_HOURS_DORMANT": 0.15,
        }
        s_share = session_shares.get(session_name.upper(), 0.50)
        session_expected_range_pips = daily_expected_range_pips * s_share

        # Trade expansion target: 0.75x to 1.2x Daily ATR based on volatility state
        trade_target_expected_pips = max(15.0, daily_expected_range_pips * 0.85)

        # Compression ratio & percentile
        baseline = max(1.0, spec.baseline_daily_atr_pips)
        compression_ratio = d_atr_pips / baseline
        # Volatility percentile: 1.0 = 50th percentile, <0.70 = compressed squeeze (<20th)
        vol_pctile = max(5.0, min(95.0, compression_ratio * 50.0))
        is_compressed = bool(compression_ratio < 0.75)

        if is_compressed:
            recommendation = (
                f"Volatility compressed ({compression_ratio:.2f}x of 30d baseline). "
                f"Energy accumulating in a squeeze. Target breakout of {daily_expected_range_pips:.1f} pips upon catalyst/killzone release."
            )
        elif is_tier1_catalyst_day:
            recommendation = (
                f"High-impact macro event day! Expanded daily expected range to {daily_expected_range_pips:.1f} pips. "
                f"Use wide volatility stops and avoid trading 15m pre/post-news release."
            )
        else:
            recommendation = (
                f"Normal liquidity regime. Expected session range: {session_expected_range_pips:.1f} pips. "
                f"Target standard expansion of {trade_target_expected_pips:.1f} pips."
            )

        pip_val_micro = spec.pip_value_usd_per_standard_lot * 0.01

        return ExpectedMoveResult(
            symbol=spec.symbol,
            asset_class=spec.asset_class.value,
            current_price=price,
            point_size=spec.point_size,
            pip_size=spec.pip_size,
            pip_value_usd_per_micro_lot=pip_val_micro,
            daily_atr_pips=d_atr_pips,
            h4_atr_pips=h4_atr_pips,
            m15_atr_pips=m15_atr_pips,
            session_expected_range_pips=session_expected_range_pips,
            daily_expected_range_pips=daily_expected_range_pips,
            weekly_expected_range_pips=weekly_expected_range_pips,
            trade_target_expected_pips=trade_target_expected_pips,
            volatility_percentile=vol_pctile,
            is_compressed=is_compressed,
            compression_ratio=compression_ratio,
            macro_catalyst_multiplier=catalyst_mult,
            recommendation=recommendation,
        )
