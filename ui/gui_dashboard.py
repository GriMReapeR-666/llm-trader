
import customtkinter as ctk
import datetime
import threading
import queue
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Set default theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class GUIDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window Setup
        self.title(" LLM Trader Pro ")
        self.geometry("1400x900")
        
        # Data State
        self.activity_stream = []
        self.metrics = {}
        self.market_status = {}
        self.log_queue = queue.Queue()
        
        # Configure Grid Layout
        self.grid_columnconfigure(1, weight=1) # Main content expands
        self.grid_rowconfigure(1, weight=1)    # Main content expands
        
        # --- Sidebar ---
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, rowspan=4, sticky="nsew")
        self.sidebar.grid_rowconfigure(4, weight=1)
        
        self.logo_label = ctk.CTkLabel(self.sidebar, text="LLM TRADER\nPRO", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))
        
        # --- Main Grid Frame (holds everything to the right of the sidebar) ---
        self.grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.grid_frame.grid(row=0, column=1, rowspan=4, columnspan=2, sticky="nsew") 
        self.grid_frame.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0) # Top bar
        self.grid_rowconfigure(1, weight=0) # Data strip
        self.grid_rowconfigure(2, weight=1) # Main split
        self.grid_rowconfigure(3, weight=0) # Bottom frame

        # --- Sidebar Content ---
        self.sidebar_metrics = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_metrics.grid(row=2, column=0, padx=10, pady=10, sticky="ew")
        
        ctk.CTkLabel(self.sidebar_metrics, text="ADVANCED METRICS", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray").pack(anchor="w", pady=(10,5))
        
        self.metric_lbls = {}
        for m in ["Win Rate", "Epsilon", "Q-Size", "Macro Align", "RSI", "ATR"]:
            f = ctk.CTkFrame(self.sidebar_metrics, fg_color="transparent")
            f.pack(fill="x", pady=2)
            ctk.CTkLabel(f, text=m, font=ctk.CTkFont(size=11), text_color="gray").pack(side="left")
            v = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=11, weight="bold"))
            v.pack(side="right")
            self.metric_lbls[m] = v
            
        # --- Top Status Bar (Mission Control integrated) ---
        self.top_bar = ctk.CTkFrame(self.grid_frame, height=40, fg_color=("gray90", "gray13"), border_width=1, border_color="gray30")
        self.top_bar.grid(row=0, column=0, padx=20, pady=(10,5), sticky="ew")
        
        self.lbl_cycle = ctk.CTkLabel(self.top_bar, text="CYCLE: IDLE", font=ctk.CTkFont(size=12, weight="bold"))
        self.lbl_cycle.pack(side="left", padx=15)
        
        self.lbl_status = ctk.CTkLabel(self.top_bar, text="STATUS: IDLE", font=ctk.CTkFont(size=12, weight="bold"), text_color="cyan")
        self.lbl_status.pack(side="left", padx=5)
        
        # Mission Progress in Top Bar
        self.missions = {}
        for m in ["Data", "Strategy", "Risk", "Exec"]:
            f = ctk.CTkFrame(self.top_bar, fg_color="transparent")
            f.pack(side="left", padx=5)
            ctk.CTkLabel(f, text=m, font=ctk.CTkFont(size=10)).pack(side="left")
            p = ctk.CTkProgressBar(f, width=40, height=6, progress_color="cyan")
            p.pack(side="left", padx=5)
            p.set(0)
            self.missions[m] = p

        self.lbl_clock = ctk.CTkLabel(self.top_bar, text="00:00:00", font=ctk.CTkFont(family="Consolas"))
        self.lbl_clock.pack(side="right", padx=15)

        # --- Data Strip (Ticket Style) ---
        self.data_strip = ctk.CTkFrame(self.grid_frame, height=60, fg_color="transparent")
        self.data_strip.grid(row=1, column=0, padx=20, pady=5, sticky="ew")
        
        def make_card(parent, title, col):
            f = ctk.CTkFrame(parent, fg_color=("gray85", "gray17"), border_width=1, border_color="gray40")
            f.pack(side="left", expand=True, fill="both", padx=4)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=10, weight="bold"), text_color="gray").pack(pady=(5,0))
            lbl = ctk.CTkLabel(f, text="--", font=ctk.CTkFont(size=16, weight="bold"))
            lbl.pack(pady=(0,5))
            return lbl

        self.card_bid = make_card(self.data_strip, "BID", 0)
        self.card_ask = make_card(self.data_strip, "ASK", 1)
        self.card_spread = make_card(self.data_strip, "SPREAD", 2)
        self.card_balance = make_card(self.data_strip, "BALANCE", 3)
        self.card_pnl = make_card(self.data_strip, "OPEN PNL", 4)
        
        # --- Main Split (Reasoning vs Right Panel) ---
        self.main_split = ctk.CTkFrame(self.grid_frame, fg_color="transparent")
        self.main_split.grid(row=2, column=0, padx=20, pady=5, sticky="nsew")
        self.main_split.grid_columnconfigure(0, weight=4) # Reasoning 80%
        self.main_split.grid_columnconfigure(1, weight=1) # Right Panel 20%
        self.main_split.grid_rowconfigure(0, weight=1)
        
        # Reasoning Panel (Chat Style) - Full Height
        self.reasoning_frame = ctk.CTkFrame(self.main_split, border_width=1, border_color="gray30")
        self.reasoning_frame.grid(row=0, column=0, padx=(0,10), pady=0, sticky="nsew")
        
        r_header = ctk.CTkFrame(self.reasoning_frame, height=30, fg_color="transparent")
        r_header.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(r_header, text=" REASONING CORE", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(r_header, text="LIVE ANALYSIS", text_color="green", font=ctk.CTkFont(size=10, weight="bold")).pack(side="right")
        
        self.reasoning_scroll = ctk.CTkScrollableFrame(self.reasoning_frame, fg_color="transparent")
        self.reasoning_scroll.pack(expand=True, fill="both", padx=5, pady=5)
        
        # Right Panel Container (Tabs + Positions)
        self.right_panel = ctk.CTkFrame(self.main_split, fg_color="transparent")
        self.right_panel.grid(row=0, column=1, sticky="nsew")
        self.right_panel.grid_rowconfigure(0, weight=3) # Tabs take 75% height
        self.right_panel.grid_rowconfigure(1, weight=1) # Positions take 25% height
        self.right_panel.grid_columnconfigure(0, weight=1)

        # Right Tabs (Activity, News, Prompts, Settings)
        self.right_tabs = ctk.CTkTabview(self.right_panel, border_width=1, border_color="gray30")
        self.right_tabs.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")
        
        self.tab_activity = self.right_tabs.add("Activity")
        self.tab_news = self.right_tabs.add("News")
        self.tab_prompts = self.right_tabs.add("Prompts")
        self.tab_settings = self.right_tabs.add("Settings")
        
        # Activity Stream
        self.txt_activity = ctk.CTkTextbox(self.tab_activity, font=ctk.CTkFont(family="Consolas", size=11), wrap="word", state="disabled")
        self.txt_activity.pack(expand=True, fill="both", padx=5, pady=5)
        
        # News/Calendar
        self.news_scroll = ctk.CTkScrollableFrame(self.tab_news)
        self.news_scroll.pack(expand=True, fill="both", padx=5, pady=5)
        
        # Prompts
        self.txt_prompts = ctk.CTkTextbox(self.tab_prompts, font=ctk.CTkFont(family="Consolas", size=10), wrap="word")
        self.txt_prompts.pack(expand=True, fill="both", padx=5, pady=5)
        
        # Settings (Interactive Menu Mirror)
        self.scroll_settings = ctk.CTkScrollableFrame(self.tab_settings)
        self.scroll_settings.pack(expand=True, fill="both", padx=5, pady=5)
        
        def add_setting(label, Options=None, Default=None):
            f = ctk.CTkFrame(self.scroll_settings, fg_color="transparent")
            f.pack(fill="x", pady=5)
            ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
            if Options:
                w = ctk.CTkOptionMenu(f, values=Options)
                if Default: w.set(Default)
                w.pack(fill="x", pady=(2,0))
                return w
            else:
                w = ctk.CTkEntry(f)
                if Default: w.insert(0, str(Default))
                w.pack(fill="x", pady=(2,0))
                return w
                
        self.set_mode = add_setting("Trading Mode", ["Paper", "Demo", "Live"], "Paper")
        self.set_symbol = add_setting("Symbol", ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD", "AUTO"], "EURUSD")
        self.set_timeframe = add_setting("Timeframe", ["M1", "M5", "M15", "H1", "H4", "AUTO"], "M15")
        self.set_model = add_setting("LLM Model", ["Mistral 7B", "Claude 3.5 Sonnet", "GPT-4 Turbo", "Llama 3"], "Mistral 7B")
        self.set_risk = add_setting("Risk per Trade (%)", Default="1.0")
        
        ctk.CTkButton(self.scroll_settings, text="Apply & Restart", fg_color="darkred", hover_color="red").pack(pady=20, fill="x")

        # --- Positions Panel (Now in Right Sidebar) ---
        self.positions_frame = ctk.CTkFrame(self.right_panel, border_width=1, border_color="gray30")
        self.positions_frame.grid(row=1, column=0, padx=0, pady=(10,0), sticky="nsew")
        
        ctk.CTkLabel(self.positions_frame, text="OPEN POSITIONS", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", padx=10, pady=5)
        
        self.lbl_positions = None # Not used as placeholder anymore
        self.positions_scroll = ctk.CTkScrollableFrame(self.positions_frame, fg_color="transparent")
        self.positions_scroll.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Start update loop
        self.after(100, self.update_clock)
        self.after(200, self.process_log_queue)

    # --- Core Functionality ---
    
    def update_clock(self):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.lbl_clock.configure(text=now)
        self.after(1000, self.update_clock)
        
    def toggle_theme(self):
        if self.theme_switch.get() == "on":
            ctk.set_appearance_mode("Dark")
        else:
            ctk.set_appearance_mode("Light")
            
    def stop_trading(self):
        self.log("Stopping trading...", "WARNING")
        # In a real scenario, this would set a flag in the trader thread
        
    def process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.txt_activity.configure(state="normal")
                self.txt_activity.insert("end", msg + "\n")
                self.txt_activity.see("end")
                self.txt_activity.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(200, self.process_log_queue)

    # --- Adapter Methods (matching TUI) ---
    
    def log(self, message: str, level: str = "INFO"):
        # Helper to colorize based on level (basic text implementation)
        prefix = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [{level}] "
        full_msg = prefix + message
        self.log_queue.put(full_msg)

    def add_activity(self, type: str, message: str, metadata: Optional[Dict] = None):
        icon = "ℹ"
        if type == "BUY": icon = ""
        elif type == "SELL": icon = "$"
        elif type == "ERROR": icon = ""
        
        meta = f" | {metadata}" if metadata else ""
        self.log(f"{icon} {message}{meta}", type)

    def update_header(self, symbol: str, cycle: int, total_cycles: int, model_name: str):
        self.lbl_status.configure(text=f"ACTIVE: {symbol}")
        self.lbl_cycle.configure(text=f"Cycle {cycle}/{total_cycles}")
        
    def update_market_data(self, toon: Dict[str, Any]):
        dom = toon.get("dom", {})
        self.card_bid.configure(text=f"{dom.get('best_bid', 0):.5f}")
        self.card_ask.configure(text=f"{dom.get('best_ask', 0):.5f}")
        self.card_spread.configure(text=f"{dom.get('spread', 0):.5f}")
        
        # Colorize imbalance?
        imb = dom.get('imbalance', 0)
        # We can add an imbalance card or update one of the others

    def update_account(self, info: Dict[str, Any]):
        # Fallback if called directly
        bal = info.get("balance", 0.0)
        eq = info.get("equity", 0.0)
        pnl = eq - bal
        
        self.card_balance.configure(text=f"${bal:,.2f}")
        self.card_pnl.configure(text=f"${pnl:,.2f}", text_color="green" if pnl >= 0 else "red")

    def update_decision(self, decision: Dict[str, Any]):
        if not decision: return
        
        reasoning = decision.get("reasoning", "")
        scenarios = decision.get("confluence_explanation", "")
        
        # Get Model Name
        model_name = "AI Model"
        if hasattr(self, "header_info"):
            model_name = self.header_info.get("model", "AI Model")
            
        # Create Message Bubble
        msg_frame = ctk.CTkFrame(self.reasoning_scroll, fg_color=("gray85", "gray25"))
        msg_frame.pack(fill="x", pady=5, padx=5)
        
        # Header: Icon + Model Name + Time
        header_text = f" {model_name}  •  {datetime.datetime.now().strftime('%H:%M:%S')}"
        ctk.CTkLabel(msg_frame, text=header_text, font=ctk.CTkFont(size=11, weight="bold"), text_color="cyan").pack(anchor="w", padx=10, pady=(5,0))
        
        # Content
        full_text = f"{reasoning}\n\n[SCENARIOS]\n{scenarios}" if scenarios else reasoning
        
        content = ctk.CTkLabel(msg_frame, text=full_text, justify="left", wraplength=500, font=ctk.CTkFont(family="Consolas", size=13))
        content.pack(anchor="w", padx=10, pady=(5,10))
        
        # Update Prompts Tab
        if "user_prompt" in decision:
            u_prompt = decision.get("user_prompt", "")
            s_prompt = decision.get("system_prompt", "") # Optional
            
            self.txt_prompts.delete("1.0", "end")
            self.txt_prompts.insert("end", f"--- SYSTEM PROMPT ---\n{s_prompt[:500]}...\n\n")
            self.txt_prompts.insert("end", f"--- USER PROMPT (DATA) ---\n{u_prompt}")
        
        # New: Update RL metrics from decision if present
        if "rl_suggestion" in decision:
            rl = decision["rl_suggestion"]
            # Map RL keys to labels
            if "win_rate" in rl: self.metric_lbls["Win Rate"].configure(text=f"{rl['win_rate']*100:.1f}%")
            if "epsilon" in rl: self.metric_lbls["Epsilon"].configure(text=f"{rl['epsilon']:.2f}")
            if "q_table_size" in rl: self.metric_lbls["Q-Size"].configure(text=str(rl['q_table_size']))
            
        # Auto-scroll (simple hack: update idle tasks then scroll)
        self.reasoning_scroll.update_idletasks()
        try:
            self.reasoning_scroll._parent_canvas.yview_moveto(1.0)
        except Exception as exc:
            logger.debug("metrics export failed: %s", exc)

    def update_technicals(self, indicators: Dict[str, Any]):
        if not indicators: return 
        # Extract last values
        def get_val(v): return v[-1] if isinstance(v, list) else v
        
        rsi = get_val(indicators.get("rsi_14", 0))
        atr = get_val(indicators.get("atr_14", 0))
        
        self.metric_lbls["RSI"].configure(text=f"{rsi:.1f}")
        self.metric_lbls["ATR"].configure(text=f"{atr:.5f}")

    def update_multi_timeframe(self, mtf_data: Dict[str, Any]):
        if not isinstance(mtf_data, dict):
            return
        trends = []
        for timeframe, values in mtf_data.items():
            if not isinstance(values, dict):
                continue
            trend = str(values.get("trend", "UNKNOWN")).upper()
            if trend in {"BULLISH", "BEARISH", "NEUTRAL"}:
                trends.append(trend)
        if trends:
            aligned = sum(1 for trend in trends if trend == trends[0]) / len(trends)
            self.metrics["mtf_alignment"] = aligned
            if "Macro Align" in self.metric_lbls:
                self.metric_lbls["Macro Align"].configure(text=f"{aligned * 100:.0f}%")
        self.metrics["multi_timeframe"] = mtf_data
        self.log("MTF updated: " + ", ".join(f"{tf}={str(values.get('trend', 'UNKNOWN')).upper()}"
                                              for tf, values in mtf_data.items() if isinstance(values, dict)), "DATA")

    def update_execution_log(self, logs: List[str]):
        if logs:
            self.log(logs[-1], "EXEC")
            
    def update_metrics(self):
        """Render the latest durable metric snapshot without inventing values."""
        if not isinstance(self.metrics, dict):
            return
        labels = {
            "win_rate": lambda value: f"{float(value) * 100:.1f}%",
            "epsilon": lambda value: f"{float(value):.2f}",
            "q_table_size": lambda value: str(int(value)),
            "macro_align": lambda value: f"{float(value) * 100:.0f}%",
            "mtf_alignment": lambda value: f"{float(value) * 100:.0f}%",
            "rsi": lambda value: f"{float(value):.1f}",
            "atr": lambda value: f"{float(value):.5f}",
        }
        target_labels = {
            "win_rate": "Win Rate", "epsilon": "Epsilon", "q_table_size": "Q-Size",
            "macro_align": "Macro Align", "mtf_alignment": "Macro Align", "rsi": "RSI", "atr": "ATR",
        }
        for key, formatter in labels.items():
            value = self.metrics.get(key)
            target = target_labels[key]
            if value is None or target not in self.metric_lbls:
                continue
            try:
                self.metric_lbls[target].configure(text=formatter(value))
            except (TypeError, ValueError):
                self.metric_lbls[target].configure(text="--")
        self.update_positions()
        
    def update_positions(self):
        # Clear existing
        for widget in self.positions_scroll.winfo_children():
            widget.destroy()
            
        if not self.metrics.get("positions"):
             ctk.CTkLabel(self.positions_scroll, text="No Open Positions").pack()
             return

        # Header
        h = ctk.CTkFrame(self.positions_scroll)
        h.pack(fill="x")
        ctk.CTkLabel(h, text="SYMBOL", width=80).pack(side="left")
        ctk.CTkLabel(h, text="VOL", width=60).pack(side="left")
        ctk.CTkLabel(h, text="PnL", width=80).pack(side="left")
        
        for pos in self.metrics.get("positions", []):
            row = ctk.CTkFrame(self.positions_scroll)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=pos.get("symbol", "?"), width=80).pack(side="left")
            ctk.CTkLabel(row, text=f"{pos.get('volume', 0):.2f}", width=60).pack(side="left")
            pnl = pos.get("profit", 0.0)
            c = "green" if pnl >= 0 else "red"
            ctk.CTkLabel(row, text=f"{pnl:.2f}", text_color=c, width=80).pack(side="left")
        
    # TUI specific methods stubbed
    def update(self):
        # Refresh metrics that might be set directly
        macro = self.metrics.get("macro_align", 0.5)
        if "Macro Align" in self.metric_lbls:
            self.metric_lbls["Macro Align"].configure(text=f"{macro*100:.0f}%")
    
    def start(self): 
        self.log("Dashboard started by Trader", "INFO")
        
    def stop(self): 
        self.log("Dashboard stopped by Trader", "INFO")
        # Do not quit app here, just maybe update status

    def run_app(self):
        self.mainloop()

# Global instance for compatibility
dashboard = GUIDashboard()

