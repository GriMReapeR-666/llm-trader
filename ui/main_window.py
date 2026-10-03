import webview
import threading
import sys
import time
import os
import logging

# Ensure current directory and project root are in path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_current_dir)
for _p in (_project_root, os.path.join(_project_root, "operations"), os.path.join(_project_root, "research"), os.path.join(_project_root, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gui_api import start_api_server
from shared_state import state
import mt5_trader

def run_server():
    print("Starting API Server...")
    start_api_server(port=8000)

def run_trader_safe():
    print("Starting Trader Logic...")
    # Set default arguments for the session
    # We default to paper trading EURUSD M15
    sys.argv = [
        "mt5_trader.py", 
        "llm-trade", 
        "--symbol", "EURUSD", 
        "--timeframe", "M15", 
        "--paper",
        "--model", "meta/llama-3.2-11b-vision-instruct"
    ]
    
    try:
        mt5_trader.main()
    except SystemExit:
        print("Trader exited.")
    except Exception as e:
        print(f"Trader crashed: {e}")
        logging.getLogger("Launcher").error(f"Trader crash: {e}")

def main():
    # 1. Start API Server
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 2. Start Trader Logic - DISABLED (Started via API now)
    # trader_thread = threading.Thread(target=run_trader_safe, daemon=True)
    # trader_thread.start()

    # 3. Update Status
    state.active_narrative = "Launching Alpha Arena UI..."
    state.is_connected = True

    # 4. Wait for Server
    def wait_for_server():
        import requests
        print("Waiting for API Server to be ready...", end="", flush=True)
        retries = 0
        while retries < 30:
            try:
                requests.get("http://127.0.0.1:8000/api/status", timeout=1)
                print(" Ready!")
                return True
            except:
                time.sleep(0.5)
                print(".", end="", flush=True)
                retries += 1
        print(" Server timed out!")
        return False

    if wait_for_server():
        # 5. Launch WebView
        print("Launching UI Window...")
        webview.create_window(
            'LLM Trader Pro', 
            'http://127.0.0.1:8000', 
            width=1400, 
            height=900, 
            background_color='#000000',
            text_select=True
        )
        webview.start(debug=True)
    else:
        print("Failed to start API server. Exiting.")

if __name__ == '__main__':
    main()
