# Critical Bug Fixes - Implementation Guide

## Bug 1: Position Size Calculation (43.44 lots)

**Issue**: `calc_position_size` returning massive position sizes
**Root Cause**: Likely incorrect formula or missing validation
**Fix**: 
- Cap forex positions at 1.0 lot maximum
- Add account balance validation
- Use proper risk calculation: `position_size = (account_balance * risk_pct) / (pip_value * stop_loss_pips)`

## Bug 2: Confluence Analysis - SMA Not Defined

**Issue**: `name 'sma' is not defined` error in confluence calculation
**Root Cause**: Code references SMA but it's not calculated
**Fix**: Remove SMA dependency, use existing indicators:
- RSI (already calculated)
- MACD (already calculated)  
- HMA (already calculated)
- ATR (already calculated)
- Orderbook imbalance (already available)

**New Confluence Formula**:
```python
confluence_score = 0
# RSI alignment (2 points)
if 30 < rsi < 70: confluence_score += 1
if (rsi > 50 and trend == "UP") or (rsi < 50 and trend == "DOWN"): confluence_score += 1

# MACD alignment (2 points)
if macd_hist > 0 and trend == "UP": confluence_score += 2
elif macd_hist < 0 and trend == "DOWN": confluence_score += 2

# HMA trend (2 points)
if price > hma and trend == "UP": confluence_score += 2
elif price < hma and trend == "DOWN": confluence_score += 2

# Volume confirmation (1 point)
if volume_ratio > 1.2: confluence_score += 1

# Orderbook imbalance (2 points)
if orderbook_imbalance > 0.6: confluence_score += 2
```

## Bug 3: BTCUSD Symbol Selection

**Issue**: LLM keeps selecting BTCUSD which isn't available
**Fix**: 
- Remove BTCUSD from available_pairs list in market scanner
- Add symbol validation before LLM selection
- Update to only include verified available symbols

## Bug 4: LLM Reasoning Fields

**Status**: ✅ FIXED (prompt updated)
**Verification**: Test on next run

## Implementation Priority:
1. Fix position sizing (CRITICAL - blocks all trades)
2. Fix confluence calculation (HIGH - affects decision quality)
3. Fix symbol selection (MEDIUM - causes errors but not blocking)
4. Verify LLM reasoning (LOW - cosmetic)
