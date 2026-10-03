
import sys
import threading
import time
import argparse

# Import the dashboard
from gui_dashboard import dashboard, GUIDashboard

# Import the trader (we will patch it)
import mt5_trader

def trader_thread(args):
    """Worker thread to run the trading logic"""
    time.sleep(1) # Give GUI time to show up
    
    # Patch sys.argv for mt5_trader.main to parse
    sys.argv = args
    
    try:
        dashboard.log("Initializing Trader...", "SYSTEM")
        mt5_trader.main()
    except SystemExit:
        dashboard.log("Trader exited.", "SYSTEM")
    except Exception as e:
        dashboard.log(f"Trader crashed: {e}", "ERROR")

def main():
    # Parse launcher arguments (or default to some)
    # We construct the arguments for the underlying mt5_trader
    
    # Example default args: run llm-trade on EURUSD with M15 using paper mode (safe default)
    trader_args = [
        "mt5_trader.py", 
        "llm-trade", 
        "--symbol", "EURUSD", 
        "--timeframe", "M15", 
        "--paper", 
        "--tui",        # We enable TUI flag so it tries to use 'dashboard'
        "--loops", "5"
    ]
    
    print("Launching GUI...")
    
    # Monkey-patch mt5_trader.dashboard to use our GUI instance
    # The 'dashboard' imported from gui_dashboard is already an instance of GUIDashboard
    mt5_trader.dashboard = dashboard
    # Also patch TUI_AVAILABLE to ensure it picks it up
    mt5_trader.TUI_AVAILABLE = True
    
    # Start Trader Thread
    t = threading.Thread(target=trader_thread, args=(trader_args,), daemon=True)
    t.start()
    
    # Run GUI Main Loop (Must be in main thread)
    dashboard.run_app()

if __name__ == "__main__":
    main()
