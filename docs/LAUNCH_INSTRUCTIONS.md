# 🚀 How to Launch

## Just Double-Click: `START_TRADING.bat`

That's it! No commands needed.

### What Happens:
1. ✅ Fixes Windows encoding
2. ✅ Launches interactive TUI menu
3. ✅ You configure everything in the menu:
   - Symbol selection
   - Timeframe
   - Model selection
   - Trading mode (demo/live/paper)
   - Risk settings
   - Number of cycles

### The Interactive Menu Shows:
- 📊 Symbol picker (or AUTO mode)
- ⏱️ Timeframe selector (M1, M5, M15, M30, H1, H4, D1)
- 🤖 LLM model selector (interactive)
- 🎯 Trading mode (DEMO/LIVE/PAPER)
- 📈 Risk per trade
- 🔄 Continuous vs fixed cycles
- ⚙️ Advanced options (mock LLM, show metrics)

### During Trading, TUI Shows:
- 📊 Open positions table (live updates)
- 💰 Account balance & P&L
- 📈 Technical indicators
- 📰 News & sentiment
- 🤖 LLM reasoning
- 🎯 RL learning state
- ⚠️ All risk management alerts

### All P0/P1 Fixes Active:
- ✅ Max 3 positions
- ✅ 70% margin cap
- ✅ Correlation blocking
- ✅ Trailing stops
- ✅ Break-even stops
- ✅ Drawdown protection

**Just double-click START_TRADING.bat and go!** 🎉

---

## Alternative (if batch file doesn't work):

Open terminal and run:
```bash
python launch_trading.py
```

Same result - full interactive TUI, no commands needed.
