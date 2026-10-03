"""Module 10: Market Structure Edges — Structural Flows, Microstructure & Calendar Anomalies.

Implements the ABSENT + STUB concepts from the MASTER_SOURCE_OF_TRUTH:

1. Dealer Gamma Exposure (GEX) — Delta-hedging flow dynamics
2. Cumulative Volume Delta (CVD) — Aggressive vs passive order flow
3. HAR-RV — Heterogeneous Autoregressive Realized Volatility model
4. Index Rebalancing Signal — Front-running reconstitution flows
5. Cross-Sectional Momentum (12-1) — Jegadeesh-Titman relative momentum
6. Market Profile / VPOC — Volume-weighted value area
7. Merger Arbitrage — Deal spread calculator
8. Turn-of-Month — Calendar anomaly
9. Crypto Funding Rate — Perpetual basis arbitrage
10. Roll Model — Bid-ask spread from autocovariance

All functions: execution_authority=False, advisory only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# 1. Dealer Gamma Exposure (GEX)
# ---------------------------------------------------------------------------

@dataclass
class GammaRegimeResult:
    regime: str  # POSITIVE_GEX, NEGATIVE_GEX, NEUTRAL
    gamma_exposure: float  # Net gamma in notional terms
    dealer_delta_hedge_bias: str  # "SELL_RALLIES" or "BUY_DIPS"
    volatility_forecast: str  # "SUPPRESSED" or "AMPLIFIED"
    zero_gamma_strike: Optional[float]
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_gamma_regime(
    strikes: Optional[List[float]] = None,
    open_interest: Optional[List[float]] = None,
    call_put_flag: Optional[List[str]] = None,
    spot_price: Optional[float] = None,
    underlying_vol: float = 0.01,
) -> GammaRegimeResult:
    """Estimate dealer net gamma exposure from options open interest.

    Uses the simplified GEX approximation:
        GEX_i = OI_i * Delta_i * Gamma_i * Spot^2 * 0.01
    where Gamma_i is estimated from Black-Scholes approximation.

    Positive GEX → dealers sell rallies, buy dips (suppress vol)
    Negative GEX → dealers buy rallies, sell dips (amplify vol)
    """
    if not strikes or not open_interest or spot_price is None or spot_price <= 0:
        return GammaRegimeResult(
            regime="NEUTRAL", gamma_exposure=0.0, dealer_delta_hedge_bias="UNKNOWN",
            volatility_forecast="UNKNOWN", zero_gamma_strike=None, confidence=0.0,
        )

    strikes_arr = np.array(strikes, dtype=float)
    oi_arr = np.array(open_interest, dtype=float)
    spot = float(spot_price)
    is_call = np.array([str(f).upper().startswith("C") for f in (call_put_flag or ["C"] * len(strikes))])

    total_gex = 0.0
    weighted_strike = 0.0
    for i in range(len(strikes_arr)):
        s = strikes_arr[i]
        oi = oi_arr[i]
        if s <= 0 or oi <= 0:
            continue
        moneyness = s / spot
        # BS gamma approximation: Gamma ≈ N'(d1) / (S * sigma * sqrt(T))
        # Simplified: gamma ≈ exp(-0.5 * (ln(moneyness))^2) / (s * underlying_vol * sqrt(1/365))
        d1_component = math.exp(-0.5 * (math.log(max(moneyness, 0.01))) ** 2)
        gamma_est = d1_component / max(s * underlying_vol * 0.0527, 1e-8)  # ~5 DTE sqrt
        # Delta approximation: calls ~0.5 * (1 + erf(d1/sqrt(2))), puts = calls - 1
        delta_est = 0.5 * (1 + math.erf(math.log(max(moneyness, 0.01)) / math.sqrt(2)))
        if not is_call[i]:
            delta_est -= 1.0
        # Dealer short gamma: dealers are typically net short options
        gex_i = -oi * delta_est * gamma_est * spot * spot * 0.01
        total_gex += gex_i
        weighted_strike += gex_i * s

    # Find zero-gamma strike (approximate)
    zero_gamma = float(weighted_strike / total_gex) if abs(total_gex) > 1e-6 else spot

    if total_gex > 1e-6:
        regime = "POSITIVE_GEX"
        bias = "SELL_RALLIES"
        vol_forecast = "SUPPRESSED"
    elif total_gex < -1e-6:
        regime = "NEGATIVE_GEX"
        bias = "BUY_DIPS"
        vol_forecast = "AMPLIFIED"
    else:
        regime = "NEUTRAL"
        bias = "NEUTRAL"
        vol_forecast = "NEUTRAL"

    return GammaRegimeResult(
        regime=regime,
        gamma_exposure=round(float(total_gex), 2),
        dealer_delta_hedge_bias=bias,
        volatility_forecast=vol_forecast,
        zero_gamma_strike=round(zero_gamma, 2),
        confidence=min(1.0, abs(total_gex) / max(oi_arr.sum() * spot * 0.01, 1e-6)),
    )


# ---------------------------------------------------------------------------
# 2. Cumulative Volume Delta (CVD)
# ---------------------------------------------------------------------------

@dataclass
class CVDResult:
    cvd_current: float
    cvd_slope: float  # Rate of change
    absorption_detected: bool
    price_vs_cvd_divergence: str  # "BULLISH_DIVERGENCE", "BEARISH_DIVERGENCE", "CONVERGENT"
    net_buy_pressure: float  # [-1, 1]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_cvd(
    trade_prices: Optional[List[float]] = None,
    trade_sizes: Optional[List[float]] = None,
    trade_sides: Optional[List[str]] = None,  # "buy" or "sell" or tick direction
    bid_prices: Optional[List[float]] = None,
    ask_prices: Optional[List[float]] = None,
    window: int = 100,
) -> CVDResult:
    """Calculate Cumulative Volume Delta from trade data.

    Uses tick rule classification when sides not provided:
    - Trade at ask = buy (+size)
    - Trade at bid = sell (-size)
    - Trade at mid = half buy, half sell
    """
    n = len(trade_prices or [])
    if n < 2:
        return CVDResult(cvd_current=0.0, cvd_slope=0.0, absorption_detected=False,
                         price_vs_cvd_divergence="CONVERGENT", net_buy_pressure=0.0)

    prices = np.array(trade_prices, dtype=float)
    sizes = np.array(trade_sizes or [1.0] * n, dtype=float)

    if trade_sides:
        deltas = np.where(
            np.array([str(s).lower() for s in trade_sides]) == "buy", sizes,
            np.where(np.array([str(s).lower() for s in trade_sides]) == "sell", -sizes, 0.0)
        )
    elif bid_prices and ask_prices:
        bids = np.array(bid_prices, dtype=float)
        asks = np.array(ask_prices, dtype=float)
        mids = (bids + asks) / 2
        # Tick rule: trade above mid = buy, below = sell
        deltas = np.where(prices > mids, sizes, np.where(prices < mids, -sizes, 0.0))
    else:
        # No side info: use tick rule from price changes
        price_diffs = np.diff(prices, prepend=prices[0])
        deltas = np.where(price_diffs > 0, sizes, np.where(price_diffs < 0, -sizes, 0.0))

    cvd = np.cumsum(deltas)
    cvd_current = float(cvd[-1])

    # Slope: linear regression of CVD over window
    w = min(window, len(cvd))
    x = np.arange(w, dtype=float)
    y = cvd[-w:]
    slope = float(np.polyfit(x, y, 1)[0]) if w > 2 else 0.0

    # Absorption: CVD rises but price fails to follow (or vice versa)
    price_change = float(prices[-1] - prices[max(0, -w)])
    cvd_change = float(cvd[-1] - cvd[max(0, -w)])
    absorption = abs(cvd_change) > 0 and abs(price_change) < 0.001 * abs(cvd_change)

    # Divergence
    if cvd_change > 0 and price_change < 0:
        divergence = "BULLISH_DIVERGENCE"  # Buyers absorbing, price held down
    elif cvd_change < 0 and price_change > 0:
        divergence = "BEARISH_DIVERGENCE"  # Sellers absorbing, price held up
    else:
        divergence = "CONVERGENT"

    total_vol = float(np.sum(np.abs(deltas)))
    net_buy_pressure = float(np.sum(deltas) / total_vol) if total_vol > 0 else 0.0

    return CVDResult(
        cvd_current=round(cvd_current, 2),
        cvd_slope=round(slope, 4),
        absorption_detected=absorption,
        price_vs_cvd_divergence=divergence,
        net_buy_pressure=round(max(-1.0, min(1.0, net_buy_pressure)), 4),
    )


# ---------------------------------------------------------------------------
# 3. HAR-RV (Heterogeneous Autoregressive Realized Volatility)
# ---------------------------------------------------------------------------

@dataclass
class HARRVResult:
    rv_1d: float
    rv_5d: float
    rv_22d: float
    har_forecast: float
    vol_regime: str  # LOW, NORMAL, ELEVATED, HIGH
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_har_rv_forecast(
    realizations: Optional[List[float]] = None,
    vol_quantiles: Optional[Dict[str, float]] = None,
) -> HARRVResult:
    """HAR-RV model: RV_{t+1} = beta_0 + beta_1 * RV_t^(d) + beta_2 * RV_t^(w) + beta_3 * RV_t^(m) + epsilon

    Realizations should be squared returns (r_t^2) at high frequency.
    Standard coefficients from Corsi (2009): beta ≈ [0.02, 0.36, 0.31, 0.22]
    """
    if not realizations or len(realizations) < 22:
        return HARRVResult(rv_1d=0.0, rv_5d=0.0, rv_22d=0.0, har_forecast=0.0,
                           vol_regime="NORMAL", confidence=0.0)

    rv = np.array(realizations, dtype=float)
    # Standard HAR-RV windows
    rv_1d = float(np.mean(rv[-1:]))       # Daily realized vol
    rv_5d = float(np.mean(rv[-5:]))       # Weekly
    rv_22d = float(np.mean(rv[-22:]))     # Monthly

    # HAR-RV forecast (Corsi 2009 coefficients)
    beta_0 = 0.02
    beta_1 = 0.36  # Daily component
    beta_2 = 0.31  # Weekly component
    beta_3 = 0.22  # Monthly component
    har_forecast = beta_0 + beta_1 * rv_1d + beta_2 * rv_5d + beta_3 * rv_22d

    # Regime classification
    if vol_quantiles:
        q75 = vol_quantiles.get("q75", rv_22d * 1.5)
        q90 = vol_quantiles.get("q90", rv_22d * 2.0)
        q25 = vol_quantiles.get("q25", rv_22d * 0.5)
    else:
        q75 = rv_22d * 1.5
        q90 = rv_22d * 2.0
        q25 = rv_22d * 0.5

    if har_forecast >= q90:
        regime = "HIGH"
    elif har_forecast >= q75:
        regime = "ELEVATED"
    elif har_forecast <= q25:
        regime = "LOW"
    else:
        regime = "NORMAL"

    return HARRVResult(
        rv_1d=round(rv_1d, 8),
        rv_5d=round(rv_5d, 8),
        rv_22d=round(rv_22d, 8),
        har_forecast=round(har_forecast, 8),
        vol_regime=regime,
        confidence=min(1.0, len(rv) / 100.0),
    )


# ---------------------------------------------------------------------------
# 4. Index Rebalancing Signal
# ---------------------------------------------------------------------------

@dataclass
class IndexRebalResult:
    event_type: str  # ADDITION, DELETION, UNKNOWN
    expected_flow_direction: str  # BULLISH, BEARISH, NEUTRAL
    pre_announcement_drift: float  # Expected drift before effective date
    post_reconstitution_reversal: float  # Expected reversal after effective date
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assess_index_rebalancing_signal(
    days_to_effective: int = 0,
    is_addition: Optional[bool] = None,
    announcement_date: Optional[str] = None,
    effective_date: Optional[str] = None,
    current_date: Optional[str] = None,
) -> IndexRebalResult:
    """Assess index rebalancing signal based on Russell/S&P reconstitution mechanics.

    Addition → structural buying pressure before effective date
    Deletion → indiscriminate dumping on effective date
    Post-reconstitution → reversal as mechanical buying/selling dries up
    """
    if is_addition is None:
        return IndexRebalResult(event_type="UNKNOWN", expected_flow_direction="NEUTRAL",
                                pre_announcement_drift=0.0, post_reconstitution_reversal=0.0,
                                confidence=0.0)

    event_type = "ADDITION" if is_addition else "DELETION"

    # Pre-announcement drift: additions get front-run (positive), deletions front-run sold
    if days_to_effective > 0:
        # Drift peaks 5-10 days before effective date
        if days_to_effective >= 10:
            drift = 0.02 if is_addition else -0.02
        elif days_to_effective >= 5:
            drift = 0.04 if is_addition else -0.04
        else:
            drift = 0.01 if is_addition else -0.01  # Fading as date approaches
    else:
        drift = 0.0

    # Post-reconstitution reversal: additions underperform, deletions outperform over 3-6 months
    reversal = -0.06 if is_addition else 0.04

    direction = "BULLISH" if is_addition else "BEARISH"
    if days_to_effective <= 0:
        direction = "NEUTRAL"  # Event passed

    return IndexRebalResult(
        event_type=event_type,
        expected_flow_direction=direction,
        pre_announcement_drift=round(drift, 4),
        post_reconstitution_reversal=round(reversal, 4),
        confidence=min(1.0, max(0.3, 1.0 - days_to_effective / 30)) if days_to_effective > 0 else 0.0,
    )


# ---------------------------------------------------------------------------
# 5. Cross-Sectional Momentum (12-1)
# ---------------------------------------------------------------------------

@dataclass
class CrossSectionalMomentumResult:
    rankings: Dict[str, float]  # asset -> momentum score
    winners: List[str]
    losers: List[str]
    long_short_spread: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_cross_sectional_momentum(
    asset_returns: Optional[Dict[str, List[float]]] = None,
    lookback_months: int = 12,
    skip_recent_months: int = 1,
) -> CrossSectionalMomentumResult:
    """Cross-sectional momentum: Jegadeesh-Titman 12-1 signal.

    For each asset: signal = cumulative return over [t-12m, t-1m] (skip most recent month).
    Rank assets by signal; go long winners, short losers.
    Skip month 1 because of short-term reversal (liquidity shocks).
    """
    if not asset_returns or len(asset_returns) < 2:
        return CrossSectionalMomentumResult(rankings={}, winners=[], losers=[],
                                            long_short_spread=0.0)

    rankings: Dict[str, float] = {}
    for asset, returns in asset_returns.items():
        r = np.array(returns, dtype=float)
        total_bars = len(r)
        # 12 months ≈ 252 trading days, skip 1 month ≈ 21 bars
        lookback_bars = min(lookback_months * 21, total_bars)
        skip_bars = min(skip_recent_months * 21, total_bars)
        if total_bars < lookback_bars:
            # Use all available data minus skip
            if total_bars > skip_bars:
                cum_ret = float(np.prod(1 + r[:total_bars - skip_bars]) - 1)
            else:
                cum_ret = 0.0
        else:
            end_idx = total_bars - skip_bars
            start_idx = max(0, end_idx - lookback_bars)
            cum_ret = float(np.prod(1 + r[start_idx:end_idx]) - 1) if end_idx > start_idx else 0.0
        rankings[asset] = round(cum_ret, 6)

    if not rankings:
        return CrossSectionalMomentumResult(rankings={}, winners=[], losers=[],
                                            long_short_spread=0.0)

    # Rank and split into winners/losers
    sorted_assets = sorted(rankings.items(), key=lambda x: x[1], reverse=True)
    mid = len(sorted_assets) // 2
    winners = [a for a, _ in sorted_assets[:mid]]
    losers = [a for a, _ in sorted_assets[mid:]]

    avg_winner = float(np.mean([rankings[a] for a in winners])) if winners else 0.0
    avg_loser = float(np.mean([rankings[a] for a in losers])) if losers else 0.0

    return CrossSectionalMomentumResult(
        rankings=rankings,
        winners=winners,
        losers=losers,
        long_short_spread=round(avg_winner - avg_loser, 6),
    )


# ---------------------------------------------------------------------------
# 6. Market Profile / VPOC
# ---------------------------------------------------------------------------

@dataclass
class MarketProfileResult:
    vpoc: float  # Volume Point of Control
    value_area_high: float
    value_area_low: float
    value_area_volume_pct: float  # % of volume in VA
    profile_skew: str  # "BALANCED", "BUY_TAIL", "SELL_TAIL"
    virgin_vpocs: List[float]  # Unretested VPOCs
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_market_profile(
    prices: Optional[List[float]] = None,
    volumes: Optional[List[float]] = None,
    num_bins: int = 40,
    value_area_pct: float = 0.70,
    session_high: Optional[float] = None,
    session_low: Optional[float] = None,
) -> MarketProfileResult:
    """Calculate Market Profile: VPOC, Value Area High/Low, and session structure.

    VPOC = price level with highest volume
    Value Area = price range containing 70% of total volume
    """
    if not prices or not volumes or len(prices) < 5:
        return MarketProfileResult(vpoc=0.0, value_area_high=0.0, value_area_low=0.0,
                                   value_area_volume_pct=0.0, profile_skew="BALANCED",
                                   virgin_vpocs=[])

    prices_arr = np.array(prices, dtype=float)
    volumes_arr = np.array(volumes, dtype=float)

    hi = session_high or float(prices_arr.max())
    lo = session_low or float(prices_arr.min())
    if hi <= lo:
        return MarketProfileResult(vpoc=float(hi), value_area_high=float(hi),
                                   value_area_low=float(lo), value_area_volume_pct=0.0,
                                   profile_skew="BALANCED", virgin_vpocs=[])

    # Bin prices and aggregate volume
    bin_edges = np.linspace(lo, hi, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    bin_volumes = np.zeros(num_bins)
    for i in range(len(prices_arr)):
        idx = int((prices_arr[i] - lo) / (hi - lo) * num_bins)
        idx = max(0, min(num_bins - 1, idx))
        bin_volumes[idx] += volumes_arr[i]

    # VPOC
    vpoc_idx = int(np.argmax(bin_volumes))
    vpoc = float(bin_centers[vpoc_idx])

    # Value Area: expand from VPOC until 70% of volume captured
    total_vol = float(np.sum(bin_volumes))
    if total_vol <= 0:
        return MarketProfileResult(vpoc=vpoc, value_area_high=vpoc, value_area_low=vpoc,
                                   value_area_volume_pct=0.0, profile_skew="BALANCED",
                                   virgin_vpocs=[])

    target_vol = total_vol * value_area_pct
    captured = bin_volumes[vpoc_idx]
    lo_idx = vpoc_idx
    hi_idx = vpoc_idx
    while captured < target_vol and (lo_idx > 0 or hi_idx < num_bins - 1):
        expand_up = bin_volumes[hi_idx + 1] if hi_idx < num_bins - 1 else 0
        expand_down = bin_volumes[lo_idx - 1] if lo_idx > 0 else 0
        if expand_up >= expand_down and hi_idx < num_bins - 1:
            hi_idx += 1
            captured += bin_volumes[hi_idx]
        elif lo_idx > 0:
            lo_idx -= 1
            captured += bin_volumes[lo_idx]
        else:
            break

    vah = float(bin_centers[min(hi_idx, num_bins - 1)])
    val = float(bin_centers[max(lo_idx, 0)])
    va_vol_pct = captured / total_vol

    # Profile skew
    vol_below_vpoc = float(np.sum(bin_volumes[:vpoc_idx]))
    vol_above_vpoc = float(np.sum(bin_volumes[vpoc_idx + 1:]))
    if vol_below_vpoc > vol_above_vpoc * 1.3:
        skew = "BUY_TAIL"  # More volume below = buyers defending
    elif vol_above_vpoc > vol_below_vpoc * 1.3:
        skew = "SELL_TAIL"  # More volume above = sellers capping
    else:
        skew = "BALANCED"

    return MarketProfileResult(
        vpoc=round(vpoc, 5),
        value_area_high=round(vah, 5),
        value_area_low=round(val, 5),
        value_area_volume_pct=round(va_vol_pct, 4),
        profile_skew=skew,
        virgin_vpocs=[],  # Requires multi-session data to identify
    )


# ---------------------------------------------------------------------------
# 7. Merger Arbitrage
# ---------------------------------------------------------------------------

@dataclass
class MergerArbResult:
    deal_spread: float  # Spread between current price and deal price
    deal_spread_annualized: float
    implied_prob_close: float  # Market-implied deal closure probability
    risk_reward: str  # "ATTRACTIVE", "FAIR", "UNATTRACTIVE"
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_merger_arb_spread(
    target_price: float,
    deal_price: float,
    days_to_close: int = 90,
    risk_free_rate: float = 0.05,
) -> MergerArbResult:
    """Calculate merger arbitrage deal spread.

    Spread = (Deal Price - Current Price) / Current Price
    Annualized = Spread * (365 / days_to_close)
    """
    if target_price <= 0 or deal_price <= 0 or days_to_close <= 0:
        return MergerArbResult(deal_spread=0.0, deal_spread_annualized=0.0,
                               implied_prob_close=0.0, risk_reward="UNATTRACTIVE",
                               confidence=0.0)

    spread = (deal_price - target_price) / target_price
    annualized = spread * (365 / days_to_close)
    # Implied P(close) from spread: spread ≈ P(close) * (deal/target - 1) - P(fail) * max_loss
    # Simplified: market prices P(close) ≈ 1 - spread/(deal/target - 1)
    if deal_price > target_price:
        max_upside = deal_price / target_price - 1
        implied_p_close = max(0.0, min(1.0, 1.0 - spread / max(max_upside, 0.01)))
    else:
        implied_p_close = 0.0

    if annualized > 0.15:
        rr = "ATTRACTIVE"
    elif annualized > 0.08:
        rr = "FAIR"
    else:
        rr = "UNATTRACTIVE"

    return MergerArbResult(
        deal_spread=round(spread, 4),
        deal_spread_annualized=round(annualized, 4),
        implied_prob_close=round(implied_p_close, 4),
        risk_reward=rr,
        confidence=min(1.0, implied_p_close),
    )


# ---------------------------------------------------------------------------
# 8. Turn-of-Month (TOTM) Calendar Effect
# ---------------------------------------------------------------------------

@dataclass
class TurnOfMonthResult:
    in_totm_window: bool
    expected_drift: float  # Historical average drift in this window
    window_position: str  # "LAST_DAY", "FIRST_3_DAYS", "OUTSIDE"
    confidence: float
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_turn_of_month(
    current_date: Optional[datetime] = None,
    days_in_month: int = 21,
    historical_totm_avg_return: float = 0.003,
) -> TurnOfMonthResult:
    """Turn-of-Month effect: positive drift during last day + first 3 days of month.

    Driven by automated pension/401(k) inflows and corporate dividend deployment.
    """
    if current_date is None:
        current_date = datetime.now(timezone.utc)

    day_of_month = current_date.day
    # TOTM window: last trading day of prior month + first 3 trading days of current month
    # Simplified: days 1-3 and last 2 calendar days (approximating last trading day)
    if day_of_month <= 3:
        in_window = True
        position = "FIRST_3_DAYS"
    elif day_of_month >= (days_in_month // 21 * 21 - 2):  # Approximate last 2 trading days
        in_window = True
        position = "LAST_DAY"
    else:
        in_window = False
        position = "OUTSIDE"

    return TurnOfMonthResult(
        in_totm_window=in_window,
        expected_drift=round(historical_totm_avg_return, 6) if in_window else 0.0,
        window_position=position,
        confidence=0.65 if in_window else 0.0,  # Historical TOTM has ~65% hit rate
    )


# ---------------------------------------------------------------------------
# 9. Crypto Funding Rate / Basis Arbitrage
# ---------------------------------------------------------------------------

@dataclass
class CryptoFundingResult:
    funding_rate_8h: float
    funding_rate_annualized: float
    basis_spread_bps: float  # Spot vs perpetual spread in bps
    cash_and_carry_viable: bool
    edge_after_fees: float  # Net yield after exchange fees
    regime: str  # "POSITIVE_CARRY", "NEGATIVE_CARRY", "NEUTRAL"
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_crypto_funding_arb(
    spot_price: float = 0.0,
    perp_price: float = 0.0,
    funding_rate_8h: float = 0.0,
    exchange_fee_pct: float = 0.04,
) -> CryptoFundingResult:
    """Calculate crypto perpetual funding rate arbitrage.

    Cash-and-carry: long spot + short perpetual = delta-neutral yield.
    Funding rate paid every 8 hours (3x daily).
    """
    if spot_price <= 0 or perp_price <= 0:
        return CryptoFundingResult(funding_rate_8h=0.0, funding_rate_annualized=0.0,
                                   basis_spread_bps=0.0, cash_and_carry_viable=False,
                                   edge_after_fees=0.0, regime="NEUTRAL")

    # Basis = (perp - spot) / spot in bps
    basis_bps = (perp_price - spot_price) / spot_price * 10000
    # Annualized funding: rate * 3 * 365
    annualized = funding_rate_8h * 3 * 365
    # Round-trip fee: entry + exit on spot + perp = 4 * fee
    annual_fees = exchange_fee_pct * 4 * 365 / 100
    net_edge = annualized - annual_fees

    if funding_rate_8h > 0.0001:
        regime = "POSITIVE_CARRY"
    elif funding_rate_8h < -0.0001:
        regime = "NEGATIVE_CARRY"
    else:
        regime = "NEUTRAL"

    return CryptoFundingResult(
        funding_rate_8h=round(funding_rate_8h, 6),
        funding_rate_annualized=round(annualized, 4),
        basis_spread_bps=round(basis_bps, 2),
        cash_and_carry_viable=net_edge > 0.01,
        edge_after_fees=round(net_edge, 4),
        regime=regime,
    )


# ---------------------------------------------------------------------------
# 10. Roll Model — Bid-Ask Spread from Autocovariance
# ---------------------------------------------------------------------------

@dataclass
class RollModelResult:
    implied_spread: float
    effective_half_spread: float
    autocovariance_lag1: float
    spread_estimate_reliable: bool
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_spread_from_roll_model(
    trade_prices: Optional[List[float]] = None,
) -> RollModelResult:
    """Roll (1984) model: effective spread from tick-by-tick autocovariance.

    Cov(ΔP_t, ΔP_{t-1}) = -c²
    where c = half-spread
    Implied spread = 2 * sqrt(-Cov) when Cov < 0
    """
    if not trade_prices or len(trade_prices) < 10:
        return RollModelResult(implied_spread=0.0, effective_half_spread=0.0,
                               autocovariance_lag1=0.0, spread_estimate_reliable=False)

    prices = np.array(trade_prices, dtype=float)
    deltas = np.diff(prices)

    if len(deltas) < 5:
        return RollModelResult(implied_spread=0.0, effective_half_spread=0.0,
                               autocovariance_lag1=0.0, spread_estimate_reliable=False)

    # Autocovariance at lag 1
    autocov = float(np.mean(deltas[1:] * deltas[:-1]) - np.mean(deltas[1:]) * np.mean(deltas[:-1]))

    if autocov < 0:
        half_spread = math.sqrt(-autocov)
        implied_spread = 2 * half_spread
    else:
        half_spread = 0.0
        implied_spread = 0.0

    return RollModelResult(
        implied_spread=round(implied_spread, 6),
        effective_half_spread=round(half_spread, 6),
        autocovariance_lag1=round(autocov, 8),
        spread_estimate_reliable=autocov < 0,
    )


# ---------------------------------------------------------------------------
# 11. Phantom Liquidity & Quote-Stuffing Guard (P18 Yang et al. 2026)
# ---------------------------------------------------------------------------

@dataclass
class PhantomLiquidityResult:
    is_phantom: bool
    safe_to_execute: bool
    phantom_risk_level: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    quote_to_trade_ratio: float
    cancellation_ratio: float
    depth_retention_ratio: float
    reasons: List[str]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def detect_phantom_liquidity(
    quote_count: int,
    trade_count: int,
    cancellation_count: int = 0,
    depth_top5_bids: float = 0.0,
    depth_top5_asks: float = 0.0,
    prev_depth_top5: Optional[float] = None,
    max_quote_to_trade_ratio: float = 50.0,
    max_cancellation_ratio: float = 0.85,
    min_depth_retention_ratio: float = 0.40,
) -> PhantomLiquidityResult:
    """Identify phantom liquidity, quote stuffing, and sudden depth evaporation (Yang 2026)."""
    qtr = float(quote_count) / max(1, trade_count)
    cancel_rate = float(cancellation_count) / max(1, quote_count)
    current_depth = float(depth_top5_bids + depth_top5_asks)

    reasons: List[str] = []
    retention_rate = 1.0
    if prev_depth_top5 is not None and prev_depth_top5 > 0:
        retention_rate = current_depth / prev_depth_top5
        if retention_rate < min_depth_retention_ratio:
            reasons.append("depth_evaporation")

    if qtr > max_quote_to_trade_ratio:
        reasons.append("quote_stuffing")

    if cancel_rate > max_cancellation_ratio and quote_count > 50:
        reasons.append("excessive_cancellations")

    is_phantom = len(reasons) > 0
    safe_to_execute = not is_phantom

    if len(reasons) >= 2:
        risk_level = "CRITICAL"
    elif len(reasons) == 1:
        risk_level = "HIGH"
    elif qtr > max_quote_to_trade_ratio * 0.7:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return PhantomLiquidityResult(
        is_phantom=is_phantom,
        safe_to_execute=safe_to_execute,
        phantom_risk_level=risk_level,
        quote_to_trade_ratio=round(qtr, 2),
        cancellation_ratio=round(cancel_rate, 4),
        depth_retention_ratio=round(retention_rate, 4),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# 12. Algorithmic Price Impact & Order Throttling Model (P19 Banerjee 2026)
# ---------------------------------------------------------------------------

@dataclass
class PriceImpactResult:
    approved: bool
    is_throttled: bool
    proposed_size: float
    throttled_size: float
    participation_rate: float
    expected_impact_points: float
    max_slippage_points: float
    reasons: List[str]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_price_impact_and_throttle(
    proposed_size: float,
    bar_volume_adv: float,
    spot_price: float,
    daily_volatility: float = 0.015,
    max_slippage_points: float = 2.0,
    max_participation_rate: float = 0.02,
    kyle_lambda_factor: float = 0.5,
    temporary_impact_gamma: float = 0.5,
) -> PriceImpactResult:
    """Calculate expected price impact via square-root law and throttle oversized orders (Banerjee 2026)."""
    prop_size = max(0.0, float(proposed_size))
    adv = max(1e-6, float(bar_volume_adv))
    price = max(1e-6, float(spot_price))
    vol = max(1e-6, float(daily_volatility))

    part_rate = prop_size / adv
    reasons: List[str] = []

    # Almgren-Chriss / Square-Root Law of Market Impact:
    # Impact = sigma * price * gamma * sqrt(Q / ADV)
    impact_points = vol * price * temporary_impact_gamma * math.sqrt(part_rate)

    max_allowed_size_part = adv * max_participation_rate

    # If impact exceeds max_slippage_points, compute max size that satisfies slippage:
    # sqrt(Q_max / ADV) = max_slippage / (vol * price * gamma)
    denom = vol * price * temporary_impact_gamma
    if denom > 0 and max_slippage_points > 0:
        max_allowed_size_impact = adv * ((max_slippage_points / denom) ** 2)
    else:
        max_allowed_size_impact = max_allowed_size_part

    safe_cap = min(max_allowed_size_part, max_allowed_size_impact)

    if prop_size > safe_cap + 1e-9:
        throttled_size = safe_cap
        is_throttled = True
        if prop_size > max_allowed_size_part:
            reasons.append("participation_rate_exceeded")
        if impact_points > max_slippage_points:
            reasons.append("price_impact_exceeds_slippage_cap")
    else:
        throttled_size = prop_size
        is_throttled = False

    approved = (throttled_size > 0.0)

    return PriceImpactResult(
        approved=approved,
        is_throttled=is_throttled,
        proposed_size=round(prop_size, 4),
        throttled_size=round(throttled_size, 4),
        participation_rate=round(part_rate, 6),
        expected_impact_points=round(impact_points, 4),
        max_slippage_points=round(max_slippage_points, 4),
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# 13. Algorithmic Herding Circuit Breaker (P18 & P19)
# ---------------------------------------------------------------------------

@dataclass
class HerdingAlertResult:
    circuit_breaker_active: bool
    herding_score: float
    directional_bias: float
    inter_arrival_dispersion_ms: float
    alert_reasons: List[str]
    execution_authority: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_herding_circuit_breaker(
    recent_order_directions: List[str],
    order_arrival_intervals_ms: List[float],
    clustering_threshold: float = 0.75,
    fast_arrival_limit_ms: float = 100.0,
) -> HerdingAlertResult:
    """Detect synchronized directional order flow clustering across algorithmic agents (Yang 2026)."""
    if not recent_order_directions:
        return HerdingAlertResult(
            circuit_breaker_active=False,
            herding_score=0.0,
            directional_bias=0.0,
            inter_arrival_dispersion_ms=1000.0,
            alert_reasons=[],
        )

    dirs = [str(d).upper() for d in recent_order_directions]
    n = len(dirs)
    buys = sum(1 for d in dirs if d in ("BUY", "LONG"))
    sells = sum(1 for d in dirs if d in ("SELL", "SHORT"))
    max_side = max(buys, sells)
    directional_bias = max_side / n if n > 0 else 0.5

    mean_interval = float(np.mean(order_arrival_intervals_ms)) if order_arrival_intervals_ms else 1000.0

    # Herding score combines directional purity with high-frequency arrival
    time_factor = 1.0 if mean_interval < fast_arrival_limit_ms else max(0.0, 1.0 - (mean_interval / 2000.0))
    herding_score = (directional_bias * 0.7) + (time_factor * 0.3)

    alert_reasons: List[str] = []
    if directional_bias >= 0.85 and n >= 4:
        alert_reasons.append("directional_clustering")
    if mean_interval < fast_arrival_limit_ms and n >= 4:
        alert_reasons.append("high_frequency_burst")

    active = (herding_score >= clustering_threshold and len(alert_reasons) > 0)

    return HerdingAlertResult(
        circuit_breaker_active=active,
        herding_score=round(herding_score, 4),
        directional_bias=round(directional_bias, 4),
        inter_arrival_dispersion_ms=round(mean_interval, 2),
        alert_reasons=alert_reasons,
    )

