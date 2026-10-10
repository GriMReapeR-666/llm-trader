try:
    from core.shared_state import state
except ImportError:
    from core.shared_state import state
import os
import datetime
import requests
import json
import threading
import queue
import logging

_push_logger = logging.getLogger("WebDashboardAdapter")

class WebDashboardAdapter:
    def __init__(self, api_url=None):
        self.state = state
        self.api_url = api_url
        self._queue = queue.Queue(maxsize=200)
        self._worker_thread = threading.Thread(target=self._worker, daemon=True)
        self._worker_thread.start()

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            type_str, data = item
            if not self.api_url:
                self._queue.task_done()
                continue
            try:
                headers = {}
                token = os.environ.get("ENGINE_INTERNAL_TOKEN") or os.environ.get("GUI_API_TOKEN")
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                requests.post(self.api_url, json={"type": type_str, "data": data}, headers=headers, timeout=1)
            except Exception as e:
                _push_logger.debug("Dashboard push failed: %s", e)
            finally:
                self._queue.task_done()

    def _push(self, type_str, data):
        """Push update to bounded queue to avoid blocking trading loop and thread accumulation"""
        if not self.api_url: return
        try:
            self._queue.put_nowait((type_str, data))
        except queue.Full:
            _push_logger.debug("Dashboard push queue full; dropping message")

    def log(self, message: str, level: str = "INFO"):
        # Create log entry matching React interface
        log_entry = {
            "id": str(datetime.datetime.now().timestamp()),
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "confidence": None
        }
        self.state.add_log(log_entry)
        self._push("log", log_entry)

    def start(self):
        self.state.workflow_state = "IDLE"
        self.log("Web Dashboard Adapter Started", "INFO")

    def stop(self):
        msg = "System Stopped"
        self.state.active_narrative = msg
        self._push("narrative", msg)
    
    def update_header(self, symbol: str, cycle: int, total_cycles: int, model_name: str):
        self.state.cycle_count = cycle
        msg = f"Trading {symbol} with {model_name} (Cycle {cycle}/{total_cycles})"
        self.state.active_narrative = msg
        # We push narrative directly
        self._push("narrative", msg)

    def update_account(self, info: dict):
        if not info: return
        self.state.balance = info.get("balance", 0.0)
        self.state.equity = info.get("equity", 0.0)
        self.state.pnl = self.state.equity - self.state.balance
        self.state.active_account_id = str(info.get("login", "N/A"))
        self._push("account", info)

    def update_market_data(self, data: dict):
        """Update dashboard with market data snapshot"""
        if "account" in data and data["account"]:
            self.update_account(data["account"])
            
        if "news" in data:
            self.update_news(data["news"])
            
        if "positions" in data and data["positions"]:
            self.update_positions(data["positions"])

    def update_decision(self, decision: dict):
        if not decision: return
        
        reasoning = decision.get("reasoning", "")
        confidence = decision.get("confidence", 0.0)
        
        log_entry = {
            "id": str(datetime.datetime.now().timestamp()),
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
            "level": "THOUGHT",
            "message": reasoning[:200] + "..." if len(reasoning) > 200 else reasoning,
            "confidence": confidence
        }
        self.state.add_log(log_entry)
        self.state.workflow_state = "REASONING"
        self._push("log", log_entry)

    def add_activity(self, type: str, message: str, metadata: dict = None):
        self.log(f"[{type}] {message}", "INFO")

    def update_technicals(self, indicators: dict):
        if not indicators: return
        self.state.technical_indicators = indicators
        self._push("technicals", indicators)

    def update_positions(self, positions: list):
        # Transform MT5 positions to UI format
        ui_positions = []
        for p in positions:
            p_dict = p._asdict() if hasattr(p, '_asdict') else p
            # Calculate net PnL (profit + swap + commission)
            net_pnl = p_dict.get('profit', 0) + p_dict.get('swap', 0) + p_dict.get('commission', 0)
            
            ui_positions.append({
                "id": str(p_dict.get('ticket', '')),
                "symbol": p_dict.get('symbol', ''),
                "side": 'BUY' if p_dict.get('type') == 0 else 'SELL',
                "volume": p_dict.get('volume', 0),
                "entryPrice": p_dict.get('price_open', 0),
                "currentPrice": p_dict.get('price_current', 0),
                "sl": p_dict.get('sl', 0),
                "tp": p_dict.get('tp', 0),
                "swap": p_dict.get('swap', 0),
                "commission": p_dict.get('commission', 0),
                "netPnL": net_pnl,
                "openTime": str(p_dict.get('time', ''))
            })
        self.state.positions = ui_positions
        # For full decoupling we could push positions too, but starting simple

    def update_history(self, deals: list):
        # Transform history deals
        ui_history = []
        for d in deals:
             d_dict = d._asdict() if hasattr(d, '_asdict') else d
             ui_history.append({
                 "id": str(d_dict.get('ticket', '')),
                 "time": datetime.datetime.fromtimestamp(d_dict.get('time', 0)).strftime("%H:%M:%S"),
                 "symbol": d_dict.get('symbol', ''),
                 "action": f"{'BUY' if d_dict.get('type') == 0 else 'SELL'} {d_dict.get('volume')}",
                 "result": f"{'WIN' if d_dict.get('profit') > 0 else 'LOSS'} ${d_dict.get('profit', 0):.2f}"
             })
        self.state.order_history = ui_history

    def update_news(self, events: list):
        # Transform news to UI format
        ui_events = []
        for e in events:
            # Map importance
            imp = e.get('importance', 1)
            if isinstance(imp, int):
                imp_str = "HIGH" if imp >= 3 else "MEDIUM" if imp == 2 else "LOW"
            else:
                imp_str = str(imp).upper()
                
            ui_events.append({
                "id": str(e.get("time", "")), # Use time as ID if no ID
                "time": e.get("time", "")[11:16], # Extract HH:MM
                "currency": e.get("currency", ""),
                "event": e.get("event", ""),
                "importance": imp_str,
                "actual": e.get("actual", ""),
                "forecast": e.get("forecast", ""),
                "deviation": "" # Calculate if needed
            })
        self.state.news_events = ui_events

    def update_rl_stats(self, trainer):
        # Extract metrics from RLTrainer instance
        if not trainer: return

        # RLTrainer stores episode_rewards; older builds used rewards_history.
        rewards = list(getattr(trainer, "rewards_history", None)
                       or getattr(trainer, "episode_rewards", []) or [])
        # The learning curve is consumed with zip(episodes, rewards, epsilon),
        # so every series must be the same length or the curve is truncated to
        # the shortest. Pad with None (explicit unknown) rather than repeating
        # the current scalar: epsilon decays, so back-filling it would draw a
        # flat exploration curve over a real decay. The live value is exposed
        # separately as epsilon_current.
        eps_hist = list(getattr(trainer, "episode_epsilons", None)
                        or getattr(trainer, "epsilon_history", None) or [])
        if eps_hist and len(eps_hist) < len(rewards):
            eps_hist.extend([None] * (len(rewards) - len(eps_hist)))
        elif len(eps_hist) > len(rewards):
            eps_hist = eps_hist[:len(rewards)]
        elif not eps_hist and hasattr(trainer, "epsilon"):
            # No recorded trace yet. One scalar is not a per-episode history,
            # so surface it as the current value only.
            eps_hist = []

        self.state.rl_metrics["episodes"] = list(range(len(rewards)))
        self.state.rl_metrics["rewards"] = rewards
        self.state.rl_metrics["epsilon"] = eps_hist
        self.state.rl_metrics["epsilon_current"] = (
            float(trainer.epsilon) if hasattr(trainer, "epsilon") else None
        )        
        # Format Q-Table
        if hasattr(trainer, 'q_table'):
            # Convert top Q-values to list
            # q_table is Dict[str, Dict[str, float]]
            top_states = []
            for state_key, actions in trainer.q_table.items():
                best_action = max(actions, key=actions.get)
                max_q = actions[best_action]
                top_states.append({
                    "state": state_key[:20] + "...",
                    "best_action": best_action,
                    "value": max_q
                })
            # Sort by value
            top_states.sort(key=lambda x: x["value"], reverse=True)
            self.state.q_table_top = top_states[:10]
        
        # Push large payload
        self._push("rl_metrics", {
            "episodes": self.state.rl_metrics["episodes"],
            "rewards": self.state.rl_metrics["rewards"],
            "epsilon": self.state.rl_metrics["epsilon"],
            "epsilon_current": self.state.rl_metrics["epsilon_current"],
            "q_table": self.state.q_table_top
        })

    def update_heartbeat(self):
        """Update system uptime and connection status timestamp"""
        now = datetime.datetime.now()
        # Assume start time was when adapter loaded (or track it)
        if not hasattr(self, '_start_time'):
            self._start_time = now
        
        delta = now - self._start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        self.state.uptime = f"{hours:02}:{minutes:02}:{seconds:02}"
        self.state.is_connected = True # Implicit keep-alive
        self._push("heartbeat", {"uptime": self.state.uptime})

    def update_metrics(self, metrics: dict):
        self.state.system_health.update(metrics)
        if "session_pnl" in metrics:
             self.state.pnl = metrics["session_pnl"]

    def update_multi_timeframe(self, mtf_data: dict):
        if not mtf_data: return
        self.state.mtf_data = mtf_data
        self._push("mtf", mtf_data)

    def update_footer(self, message: str):
        self.state.active_narrative = message
        self._push("narrative", message)

    def update_execution_log(self, logs: list):
        for msg in logs:
            self.log(msg, "EXEC")

    def run_app(self):
        pass

# Default instance (No URL by default, requires manual init or CLI arg)
dashboard = WebDashboardAdapter()
