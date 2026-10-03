# Windows Terminal Display Fix Guide

## Problem
The TUI borders, colors, and boxes aren't rendering properly in Windows PowerShell. You're seeing garbled characters like `Γöî`, `Γöé` instead of clean boxes.

## Quick Fixes (Try in Order)

### Solution 1: Use Windows Terminal (RECOMMENDED ✅)
Windows Terminal has better UTF-8 support than PowerShell.

1. **Install Windows Terminal** (if not installed):
   - Open Microsoft Store
   - Search for "Windows Terminal"
   - Install it (free)

2. **Run the trader in Windows Terminal:**
   ```powershell
   # Open Windows Terminal, then run:
   cd "C:\Users\User\OneDrive\Desktop\selfmade game projects\llm trader"
python mt5_trader.py llm-trade --symbol EURUSD --timeframe H1 --demo --loops 1
   ```

### Solution 2: Fix PowerShell Encoding
If you want to keep using PowerShell, fix the encoding:

```powershell
# Run these commands before starting the trader:
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
chcp 65001

# Then run the trader:
python mt5_trader.py llm-trade --symbol EURUSD --timeframe H1 --demo --loops 1
```

### Solution 3: Change PowerShell Font
1. Right-click PowerShell title bar → Properties
2. Go to Font tab
3. Select **"Consolas"** or **"Cascadia Code"** or **"Cascadia Mono"**
4. Click OK
5. Restart PowerShell

### Solution 4: Install Better Font
For best results, install a modern terminal font:

1. Download **Cascadia Code** from: https://github.com/microsoft/cascadia-code/releases
2. Extract and install the .ttf files
3. Set it in PowerShell (Properties → Font)

## Verify the Fix

After applying a solution, test with this command:
```powershell
python -c "from rich.console import Console; from rich.panel import Panel; Console().print(Panel('Test ✅', border_style='green'))"
```

You should see a clean green box with "Test ✅" inside, not garbled characters.

## What You Should See

### Before Fix (Garbled):
```
ΓöîΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
Γöé Test  Γöé
ΓööΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
```

### After Fix (Clean):
```
┌──────────┐
│ Test ✅  │
└──────────┘
```

## LLM Decision Display

Once fixed, you'll see beautiful displays like:

```
╭─────────────────── 🤖 LLM Decision ───────────────────╮
│                                                        │
│  Action: BUY                                           │
│  Confidence: 75%                                       │
│  Entry: 1.15850                                        │
│  Stop Loss: 1.15650 (-20 pips)                         │
│  Take Profit: 1.16250 (+40 pips)                       │
│  Position Size: 0.50 lots                              │
│                                                        │
│  Reasoning:                                            │
│  Strong bullish momentum with RSI confirmation...      │
│                                                        │
╰────────────────────────────────────────────────────────╯
```

## Permanent Fix (Optional)

Add to your PowerShell profile to make encoding fix permanent:

```powershell
# Edit your profile:
notepad $PROFILE

# Add these lines:
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null 2>&1
```

Save and restart PowerShell.

## Alternative: View Logs in Browser

If terminal fixes don't work, the system still creates log files you can view:
- `system_test_run.log` - Contains all output
- Open in VS Code or Notepad++ for better rendering

## Still Having Issues?

The system DOES work correctly - it's only a display issue. All features are functional:
- ✅ Trading decisions being made
- ✅ Position management working
- ✅ Trailing stops updating
- ✅ Data being fetched

The log files contain all the information, just not prettily formatted.
