try:
    import MetaTrader5 as mt5
except ImportError:
    # Paper/research installs may not ship the MT5 package; every runtime
    # use below degrades to a fallback instead of crashing at import time.
    mt5 = None
import time
import json
import os
import logging
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional, Callable

try:
    from state_persistence import StoreBackedState
except ImportError:
    from core.state_persistence import StoreBackedState

class RiskManager(StoreBackedState):
    store_key = "risk_manager_state"

    def __init__(self, balance: float, risk_per_trade: float = 0.01, 
                 max_drawdown: float = 0.10, cooldown_seconds: int = 300,
                 max_trades_per_hour: int = 5, max_daily_loss: float = 0.03,
                 max_consecutive_losses: int = 5, store: Any = None,
                 equity_floor_fraction: float = 0.20,
                 state_file: str = "risk_state.json"):
        if not self._finite(balance, positive=True):
            raise ValueError("balance must be finite and positive")
        if not self._finite(risk_per_trade, positive=True) or float(risk_per_trade) > 0.02:
            raise ValueError("risk_per_trade must be a finite fraction in (0, 0.02]")
        if not self._finite(max_drawdown, positive=True) or float(max_drawdown) > 1.0:
            raise ValueError("max_drawdown must be a finite fraction in (0, 1]")
        if not self._finite(max_daily_loss, positive=True) or float(max_daily_loss) > 1.0:
            raise ValueError("max_daily_loss must be a finite fraction in (0, 1]")
        if not self._finite(equity_floor_fraction, positive=True) or float(equity_floor_fraction) > 0.5:
            raise ValueError("equity_floor_fraction must be a finite fraction in (0, 0.5]")
        if not isinstance(cooldown_seconds, int) or cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a non-negative integer")
        if not isinstance(max_trades_per_hour, int) or max_trades_per_hour <= 0:
            raise ValueError("max_trades_per_hour must be a positive integer")
        if not isinstance(max_consecutive_losses, int) or max_consecutive_losses <= 0:
            raise ValueError("max_consecutive_losses must be a positive integer")
        self.balance = float(balance)
        self.risk_per_trade = float(risk_per_trade) # User desired 1% (0.01)
        self.max_drawdown = float(max_drawdown)
        self.cooldown_seconds = cooldown_seconds
        self.max_trades_per_hour = max_trades_per_hour
        self.max_daily_loss = float(max_daily_loss) # 3% hard limit
        self.max_consecutive_losses = int(max_consecutive_losses)
        
        self.initial_balance = float(balance)
        self.equity_floor_fraction = float(equity_floor_fraction)
        self.daily_start_equity = balance
        self.weekly_start_equity = balance
        self.weekly_recovered_r = 0.0
        self.trade_timestamps: List[float] = []
        self.last_trade_time: Dict[str, float] = {}
        self.open_positions: List[Dict[str, Any]] = []
        self.peak_equity = balance
        self.state_date = ""
        self.state_week = ""
        self.store = store
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.trade_r_history: List[float] = []
        self.permanent_halt = False
        self.weekly_circuit_step_down = False
        # Portfolio heat budget in R units (sum of open |entry-sl| value over
        # planned 1R$). Compressible at runtime via set_heat_budget_r(), e.g.
        # from the copula tail-crisis state (4R normal -> 1R contagion).
        self.max_portfolio_heat_r = 4.0
        # Opportunity cost of capital hurdle in R units (Masoumian, 2026)
        self.opportunity_cost_hurdle_r: float = 0.05
        # Maximum concurrent open positions enforced per strategy/bot
        self.max_active_positions = 2
        self.managed_magics = {460001, 460002}
        self._active_positions_provider: Optional[Callable[[], int]] = None
        self._portfolio_heat_provider: Optional[Callable[[], Optional[float]]] = None
        # When daily loss or peak DD trips, open positions must flatten — not only block entries.
        self._flatten_reason: Optional[str] = None

        # Semi-Parametric Markov Chain (SPMC) strategy regime filter (Tolusic, 2026)
        try:
            from core.spmc_regime_filter import SPMCRegimeFilter
            self.spmc_filter = SPMCRegimeFilter()
        except Exception:
            self.spmc_filter = None
        
        self.state_file = state_file
        
        # Load State (Persistence)
        self.load_state()
        
        # Reset if new day / week
        now_dt = datetime.now(timezone.utc)
        self.current_date = now_dt.date().isoformat()
        self.current_week = f"{now_dt.isocalendar()[0]}-W{now_dt.isocalendar()[1]}"
        if self.state_date != self.current_date:
            self.reset_daily_limits()
        if self.state_week != self.current_week:
            self.reset_weekly_limits()

    def reset_weekly_limits(self):
        now_dt = datetime.now(timezone.utc)
        self.state_week = f"{now_dt.isocalendar()[0]}-W{now_dt.isocalendar()[1]}"
        self.weekly_circuit_step_down = False
        self.weekly_recovered_r = 0.0
        try:
            acc = mt5.account_info()
            self.weekly_start_equity = float(acc.equity) if acc else self.balance
        except Exception:
            self.weekly_start_equity = self.balance
        self.save_state()

    def reset_daily_limits(self):
        self.state_date = datetime.now().date().isoformat()
        self._flatten_reason = None
        
        # Get current equity as start point
        try:
            acc = mt5.account_info()
            self.daily_start_equity = float(acc.equity) if acc else self.balance
        except Exception:
            self.daily_start_equity = self.balance
        
        logging.getLogger("RiskManager").info(f"🔁 Daily Limits Reset. Start Equity: ${self.daily_start_equity:.2f}")
        self.save_state()

    @staticmethod
    def _parse_to_iso(value: Any) -> str:
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
                return value
            except ValueError:
                pass
        try:
            f = float(value)
            return datetime.fromtimestamp(f, timezone.utc).isoformat()
        except (TypeError, ValueError):
            return datetime.now(timezone.utc).isoformat()

    def load_state(self):
        try:
            # Single transport: store -> file (one-time migration) -> default.
            data = self._load_state_dict()
            if isinstance(data, dict) and data:
                self.daily_start_equity = data.get("start_equity", self.balance)
                self.weekly_start_equity = data.get("weekly_start_equity", self.balance)
                self.weekly_recovered_r = float(data.get("weekly_recovered_r", 0.0) or 0.0)
                self.weekly_circuit_step_down = bool(data.get("weekly_circuit_step_down", False))
                self.state_date = data.get("date", "")
                self.state_week = data.get("week", "")
                self.trade_timestamps = [self._parse_to_iso(value) for value in data.get("trade_timestamps", [])]
                self.last_trade_time = {str(key): self._parse_to_iso(value) for key, value in (data.get("last_trade_time", {}) or {}).items()}
                self.peak_equity = float(data.get("peak_equity", self.peak_equity) or self.peak_equity)
                try:
                    self.consecutive_losses = max(0, int(data.get("consecutive_losses", 0) or 0))
                except (TypeError, ValueError):
                    self.consecutive_losses = 0
                try:
                    self.consecutive_wins = max(0, int(data.get("consecutive_wins", 0) or 0))
                except (TypeError, ValueError):
                    self.consecutive_wins = 0
                try:
                    history = [float(v) for v in (data.get("trade_r_history", []) or [])]
                    self.trade_r_history = [v for v in history if math.isfinite(v)][-50:]
                except (TypeError, ValueError):
                    self.trade_r_history = []
                self.permanent_halt = bool(data.get("permanent_halt", False))
                # Adopt a persisted initial_balance ONLY when it is consistent
                # with this process's account scale (within 2x of the
                # constructor anchor). This preserves legitimate restart
                # continuity — the equity floor stays anchored to the true
                # original capital across restarts — while refusing stale
                # state written under a different account scale (e.g. test
                # pollution at 100k re-anchoring a 10k account's floor to
                # 20000 → instant false PERMANENT HALT).
                try:
                    _persisted_initial = float(data.get("initial_balance") or 0.0)
                except (TypeError, ValueError):
                    _persisted_initial = 0.0
                if _persisted_initial > 0 and self.initial_balance > 0 \
                        and 0.5 <= _persisted_initial / self.initial_balance <= 2.0:
                    self.initial_balance = _persisted_initial
                elif _persisted_initial > 0:
                    logging.getLogger("RiskManager").warning(
                        "Ignoring persisted initial_balance %.2f (inconsistent "
                        "with account anchor %.2f) — possible state from a "
                        "different account scale; floor re-anchored.",
                        _persisted_initial, self.initial_balance,
                    )
                flatten = data.get("flatten_reason")
                self._flatten_reason = str(flatten) if flatten else None
            elif self.store is not None:
                self.daily_start_equity = self.balance
                self.weekly_start_equity = self.balance
                self.weekly_recovered_r = 0.0
                self.weekly_circuit_step_down = False
                self.consecutive_losses = 0
                self.trade_timestamps = []
                self.last_trade_time = {}
                self.peak_equity = self.balance
                self._flatten_reason = None
        except Exception as exc:
            logging.getLogger("RiskManager").error("Failed to load risk state: %s", exc)
            if not getattr(self, "state_date", ""):
                self.state_date = datetime.now(timezone.utc).date().isoformat()

    def set_store(self, store: Any) -> None:
        """Attach the authoritative journal and migrate risk state into it."""
        # Skip reload if the store is the same instance (avoids double state load)
        if store is self.store:
            return
        self.store = store
        self.load_state()
        self.current_date = datetime.now().date().isoformat()
        if self.state_date != self.current_date:
            self.reset_daily_limits()
        else:
            self.save_state()

    @staticmethod
    def _finite(value: Any, *, positive: bool = False) -> bool:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return False
        return math.isfinite(number) and (not positive or number > 0)

    @staticmethod
    def _account_equity() -> Optional[float]:
        """Best-effort live equity; None when MT5 is unavailable/uninitialized."""
        try:
            acc = mt5.account_info()
            return float(acc.equity) if acc else None
        except Exception:
            return None

    @staticmethod
    def _symbol_spec(symbol: str):
        """Best-effort broker symbol specification; None when MT5 is unavailable."""
        try:
            return mt5.symbol_info(symbol)
        except Exception:
            return None

    def save_state(self):
        try:
            payload = {
                "start_equity": self.daily_start_equity,
                "weekly_start_equity": self.weekly_start_equity,
                "weekly_recovered_r": float(self.weekly_recovered_r),
                "weekly_circuit_step_down": bool(self.weekly_circuit_step_down),
                "date": self.state_date,
                "week": self.state_week,
                "trade_timestamps": list(self.trade_timestamps),
                "last_trade_time": dict(self.last_trade_time),
                "peak_equity": self.peak_equity,
                "consecutive_losses": int(self.consecutive_losses),
                "consecutive_wins": int(self.consecutive_wins),
                "trade_r_history": [float(v) for v in self.trade_r_history],
                "permanent_halt": bool(self.permanent_halt),
                "initial_balance": float(self.initial_balance),
                "flatten_reason": self._flatten_reason,
            }
            self._save_state_dict(payload)
        except Exception as e:
            logging.error(f"Failed to save risk state: {e}")

    def check_daily_drawdown(self, current_equity: Optional[float] = None) -> bool:
        """Level 1 Circuit Breaker: Returns False if daily loss >= 3.0R (e.g. 3%).
        On breach, halts new entries for 24h and marks protective flatten request.
        """
        if self.daily_start_equity <= 0:
            return True
        equity = current_equity
        if equity is None:
            equity = self._account_equity()
        if equity is None:
            equity = self.balance
        current_dd = (self.daily_start_equity - equity) / self.daily_start_equity
        if current_dd >= self.max_daily_loss:
            reason = f"daily_loss_limit:{current_dd:.2%}>={self.max_daily_loss:.2%}"
            logging.critical(
                "🛑 LEVEL 1 CIRCUIT BREAKER (DAILY -3.0R): %s >= %s. HALTING TRADING FOR 24H & FLATTENING.",
                f"{current_dd:.2%}", f"{self.max_daily_loss:.2%}",
            )
            if self._flatten_reason != reason:
                self._flatten_reason = reason
                self.save_state()
            return False
        return True

    def check_weekly_circuit_breaker(self, current_equity: Optional[float] = None) -> bool:
        """Level 2 Circuit Breaker: Weekly loss >= -6.0R cuts sizing by 50% until +2.0R recovered."""
        if self.weekly_start_equity <= 0:
            return True
        equity = current_equity
        if equity is None:
            equity = self._account_equity()
        if equity is None:
            equity = self.balance
        
        # -6.0R threshold on weekly starting equity
        max_weekly_loss_fraction = min(0.06, self.risk_per_trade * 6.0)
        weekly_dd = (self.weekly_start_equity - equity) / self.weekly_start_equity
        
        if weekly_dd >= max_weekly_loss_fraction and not self.weekly_circuit_step_down:
            self.weekly_circuit_step_down = True
            self.weekly_recovered_r = 0.0
            logging.critical(
                "🛑 LEVEL 2 CIRCUIT BREAKER (WEEKLY -6.0R): Loss %s >= %s. CUTTING POSITION SIZES BY 50%% UNTIL +2.0R RECOVERED.",
                f"{weekly_dd:.2%}", f"{max_weekly_loss_fraction:.2%}",
            )
            self.save_state()
        return True

    def get_circuit_breaker_size_multiplier(self) -> float:
        """Returns 0.50 if Level 2 weekly step-down is active, else 1.0."""
        return 0.50 if self.weekly_circuit_step_down else 1.0

    def trigger_emergency_flatten(self, reason: str = "emergency_flatten_requested") -> None:
        """Explicitly flag protective flatten of all open positions."""
        self._flatten_reason = str(reason)
        self.save_state()

    def pending_flatten_reason(self) -> Optional[str]:
        """Return flatten reason if protective close of open risk is required."""
        return self._flatten_reason

    def clear_flatten_request(self) -> None:
        if self._flatten_reason is not None:
            self._flatten_reason = None
            self.save_state()

    def record_trade_outcome(self, pnl: float) -> None:
        """Update consecutive-loss and weekly step-down circuit breakers from a closed-trade PnL."""
        try:
            pnl_value = float(pnl)
        except (TypeError, ValueError, OverflowError):
            return
        if not math.isfinite(pnl_value):
            return
        
        r_dollar = self.daily_start_equity * self.risk_per_trade
        r_mult = (pnl_value / r_dollar) if r_dollar > 0 else 0.0

        if pnl_value < 0:
            self.consecutive_losses += 1
            self.consecutive_wins = 0
        else:
            self.consecutive_wins += 1
            if pnl_value == 0:
                # Breakeven exits neither extend a win streak nor reset the
                # loss breaker, but they do end a winning run.
                self.consecutive_wins = max(0, self.consecutive_wins - 1)
            else:
                self.consecutive_losses = 0
            if self.weekly_circuit_step_down and r_mult > 0:
                self.weekly_recovered_r += r_mult
                if self.weekly_recovered_r >= 2.0:
                    self.weekly_circuit_step_down = False
                    self.weekly_recovered_r = 0.0
                    logging.info("✅ LEVEL 2 CIRCUIT BREAKER RECOVERED: +2.0R achieved. Restoring 100% position sizing.")

        if math.isfinite(r_mult):
            self.trade_r_history.append(r_mult)
            self.trade_r_history = self.trade_r_history[-50:]

            # Update Tolusic (2026) SPMC regime tracker
            if getattr(self, "spmc_filter", None) is not None:
                try:
                    self.spmc_filter.observe_trade(pnl_value > 0)
                except Exception as exc:
                    logging.getLogger("RiskManager").warning("SPMC regime filter observe_trade failed: %s", exc)

            # Recalibrate Masoumian (2026) analytical kill-switch threshold:
            # n_loss^(alpha) = ceil(ln(alpha) / ln(1 - WR))
            if len(self.trade_r_history) >= 15:
                wins = [r for r in self.trade_r_history if r > 0]
                wr = len(wins) / float(len(self.trade_r_history))
                try:
                    from core.metrics import calculate_statistical_kill_switch
                    stat_limit = calculate_statistical_kill_switch(wr, alpha=0.01)
                    self.max_consecutive_losses = max(3, min(10, stat_limit))
                except Exception as exc:
                    logging.getLogger("RiskManager").error("Statistical kill-switch calculation failed: %s", exc)
                    try:
                        from core.alert_utils import emit_alert
                        emit_alert(
                            getattr(self, "state_file", "trading_state.db").replace(".json", ".db"),
                            "STATISTICAL_KILL_SWITCH_ERROR",
                            "CRITICAL",
                            {"error": str(exc), "win_rate": wr, "source": "risk_manager"}
                        )
                    except Exception:
                        pass

        self.save_state()
        if self.consecutive_losses >= self.max_consecutive_losses:
            logging.critical(
                "🛑 CONSECUTIVE LOSS CIRCUIT BREAKER: %s losses in a row (limit %s). HALTING NEW ENTRIES.",
                self.consecutive_losses, self.max_consecutive_losses,
            )

    record_trade_result = record_trade_outcome

    def check_consecutive_loss_breaker(self) -> bool:
        """Returns False when N consecutive losses have tripped the breaker."""
        return self.consecutive_losses < self.max_consecutive_losses

    def check_equity_floor(self, current_equity: Optional[float] = None) -> bool:
        """PERMANENT HALT gate: False once equity falls below the floor fraction
        of initial capital (e.g. 20 on a 100 account at 0.20). Persists across
        restarts; only clear_permanent_halt() (manual review) can lift it.
        """
        if self.permanent_halt:
            return False
        equity = current_equity
        if equity is None:
            equity = self._account_equity()
        if equity is None:
            equity = self.balance
        floor = self.initial_balance * self.equity_floor_fraction
        if self.initial_balance > 0 and float(equity) < floor:
            self.permanent_halt = True
            reason = f"equity_floor:{float(equity):.2f}<{floor:.2f}"
            if self._flatten_reason != reason:
                self._flatten_reason = reason
                logging.critical(
                    "🛑 PERMANENT HALT (EQUITY FLOOR): %s. System redesign required.",
                    reason,
                )
                self.save_state()
            return False
        return True

    def clear_permanent_halt(self) -> bool:
        """Manual-review recovery from the equity-floor permanent halt."""
        self.permanent_halt = False
        self.save_state()
        return True

    def get_drawdown_multiplier(self, current_equity: Optional[float] = None) -> float:
        """Drawdown control ladder vs peak equity: 1.0 / 0.75 / 0.50 / 0.25 / 0.0."""
        equity = current_equity
        if equity is None:
            equity = self._account_equity()
        if equity is None:
            equity = self.balance
        if not self._finite(equity, positive=True) or not self._finite(self.peak_equity, positive=True):
            return 1.0
        dd = (float(self.peak_equity) - float(equity)) / float(self.peak_equity)
        if dd < 0.05:
            return 1.0
        if dd < 0.10:
            return 0.75
        if dd < 0.15:
            return 0.50
        if dd < 0.20:
            return 0.25
        return 0.0

    def get_streak_multiplier(self) -> float:
        """Anti-martingale streak multiplier: halve per consecutive loss,
        +20% per win after the first (cap 1.8x)."""
        if self.consecutive_losses >= 5 or self.permanent_halt:
            return 0.0
        loss_table = {0: 1.0, 1: 1.0, 2: 0.5, 3: 0.25, 4: 0.125}
        mult = loss_table.get(int(self.consecutive_losses), 0.125)
        if self.consecutive_wins >= 2:
            mult *= min(1.8, 1.0 + 0.2 * (int(self.consecutive_wins) - 1))
        return float(mult)

    def quarter_kelly_scalar(self, base_risk_fraction: Optional[float] = None,
                             min_trades: int = 20) -> float:
        """Quarter-Kelly as a reduction-only scalar in [0, 1].

        Neutral (1.0) until `min_trades` closed R-samples exist; thereafter
        scalar = clamp(quarter_f / base_risk, 0, 1). A weak/negative edge
        shrinks size toward zero but never inflates it.
        """
        base = float(base_risk_fraction or self.risk_per_trade)
        history = [v for v in self.trade_r_history if v > 0]
        losses = [abs(v) for v in self.trade_r_history if v < 0]
        n = len(history) + len(losses)
        if n < int(min_trades) or base <= 0:
            return 1.0
        p = len(history) / float(n)
        avg_win = (sum(history) / float(len(history))) if history else 0.0
        avg_loss = (sum(losses) / float(len(losses))) if losses else 1.0
        b = (avg_win / avg_loss) if avg_loss > 0 else 0.0
        try:
            from core.metrics import calculate_cost_adjusted_quarter_kelly
            quarter_f = calculate_cost_adjusted_quarter_kelly(
                win_rate=p,
                reward_risk_ratio=b,
                stop_distance_points=10.0,
                spread_points=0.0,
                commission_per_lot_dollar=0.0,
                point_value_dollar=1.0,
                max_cap=max(base, 0.02),
            )
        except Exception:
            try:
                from core.cointegration_and_compounding_governance import (
                    calculate_fractional_growth_optimal_kelly,
                )
                quarter_f = calculate_fractional_growth_optimal_kelly(
                    win_rate=p, payoff_ratio=b, fractional_multiplier=0.25,
                    max_risk_cap=max(base, 0.02),
                )
            except Exception:
                quarter_f = 0.0
        if not math.isfinite(quarter_f):
            return 0.0
        return max(0.0, min(1.0, quarter_f / base))

    def opportunity_cost_scalar(self, min_trades: int = 20) -> float:
        """Opportunity cost hurdle scalar in {0.0, 1.0} (Masoumian 2026).

        Neutral (1.0) until min_trades closed R-samples exist.
        Thereafter, evaluates rolling net expectancy: if below opportunity_cost_hurdle_r,
        returns 0.0 to prevent deploying capital into sub-hurdle performance regimes.
        """
        if len(self.trade_r_history) < int(min_trades):
            return 1.0
        recent = self.trade_r_history[-30:]
        mean_r = sum(recent) / float(len(recent))
        if mean_r < self.opportunity_cost_hurdle_r:
            return 0.0
        return 1.0

    def calculate_atr_volatility_scalar(
        self,
        current_atr: Optional[float] = None,
        baseline_atr: Optional[float] = None,
        min_scalar: float = 0.25,
        max_scalar: float = 1.75,
    ) -> float:
        """Dynamic ATR-volatility risk parity scalar.

        Scales position size inversely with normalized volatility:
            scalar = baseline_atr / current_atr
        High volatility (surging ATR) reduces size to protect capital.
        Quiet volatility (compressed ATR) modestly scales up size.
        Clamped within [min_scalar, max_scalar]. Defaults to 1.0 if unavailable.
        """
        if current_atr is None or baseline_atr is None:
            return 1.0
        try:
            c_atr = float(current_atr)
            b_atr = float(baseline_atr)
            if not math.isfinite(c_atr) or not math.isfinite(b_atr) or c_atr <= 0 or b_atr <= 0:
                return 1.0
            scalar = b_atr / c_atr
            return max(min_scalar, min(max_scalar, scalar))
        except (TypeError, ValueError, ZeroDivisionError):
            return 1.0

    def calculate_parametric_cvar(
        self,
        confidence_level: float = 0.95,
        min_samples: int = 15,
    ) -> Dict[str, Any]:
        """Calculates Parametric Gaussian Value-at-Risk (VaR) and Expected Shortfall (CVaR).

        VaR_alpha = -(mu - z_alpha * sigma)
        CVaR_alpha = -(mu - sigma * (phi(z_alpha) / (1 - alpha)))

        Operates on trade R returns from self.trade_r_history.
        """
        samples = [float(r) for r in self.trade_r_history if math.isfinite(float(r))]
        if len(samples) < min_samples:
            return {
                "available": False,
                "n_samples": len(samples),
                "mean_r": 0.0,
                "std_r": 0.0,
                "var_r": 1.0,
                "cvar_r": 1.5,
                "confidence_level": confidence_level,
                "method": "insufficient_samples_fallback",
            }

        n = len(samples)
        mu = sum(samples) / float(n)
        variance = sum((x - mu) ** 2 for x in samples) / float(max(1, n - 1))
        sigma = math.sqrt(variance) if variance > 0 else 1e-6

        # Standard normal quantiles (approximate)
        if confidence_level >= 0.99:
            z_alpha = 2.326
        else:
            z_alpha = 1.645

        # phi(z) = standard normal pdf = (1 / sqrt(2*pi)) * exp(-0.5 * z^2)
        pdf_z = (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * (z_alpha ** 2))
        alpha_tail = max(1e-4, 1.0 - confidence_level)

        var_r = max(0.0, -(mu - z_alpha * sigma))
        cvar_r = max(var_r, -(mu - sigma * (pdf_z / alpha_tail)))

        return {
            "available": True,
            "n_samples": n,
            "mean_r": round(mu, 4),
            "std_r": round(sigma, 4),
            "var_r": round(var_r, 4),
            "cvar_r": round(cvar_r, 4),
            "confidence_level": confidence_level,
            "method": "parametric_gaussian",
        }

    def check_max_drawdown(self, current_equity: Optional[float] = None) -> bool:
        """Level 3 Circuit Breaker: Peak-to-trough >= 15% liquidates all positions to cash."""
        equity = current_equity
        if equity is None:
            equity = self._account_equity()
        if equity is None:
            equity = self.balance
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity <= 0:
            return True
        peak_dd = (self.peak_equity - equity) / self.peak_equity
        if peak_dd >= self.max_drawdown:
            reason = f"peak_drawdown_limit:{peak_dd:.2%}>={self.max_drawdown:.2%}"
            logging.critical(
                "🛑 LEVEL 3 CIRCUIT BREAKER (PEAK-TO-TROUGH): %s >= %s. EMERGENCY CASH LIQUIDATION & PERFORMANCE AUDIT.",
                f"{peak_dd:.2%}", f"{self.max_drawdown:.2%}",
            )
            if self._flatten_reason != reason:
                self._flatten_reason = reason
                self.save_state()
            return False
        return True

    def check_cooldown(self, symbol: str) -> bool:
        last_iso = self.last_trade_time.get(symbol)
        if not last_iso:
            return True
        try:
            last_dt = datetime.fromisoformat(last_iso.replace('Z', '+00:00'))
            last_ts = last_dt.timestamp()
        except ValueError:
            last_ts = 0
        return (time.time() - last_ts) > self.cooldown_seconds

    def check_cooldown_with_reason(self, symbol: str) -> Tuple[bool, str]:
        last_iso = self.last_trade_time.get(symbol)
        if not last_iso:
            return True, "0"
        try:
            last_dt = datetime.fromisoformat(last_iso.replace('Z', '+00:00'))
            last_ts = last_dt.timestamp()
        except ValueError:
            last_ts = 0
            
        elapsed = time.time() - last_ts
        remaining = self.cooldown_seconds - elapsed
        if remaining > 0:
            return False, f"{int(remaining)}"
        return True, "0"

    def check_rate_limit(self) -> bool:
        now = time.time()
        recent = []
        for t_iso in self.trade_timestamps:
            try:
                t_dt = datetime.fromisoformat(t_iso.replace('Z', '+00:00'))
                t_ts = t_dt.timestamp()
            except ValueError:
                t_ts = 0
            if now - t_ts < 3600:
                recent.append(t_iso)
        # Only persist when timestamps were actually pruned to avoid
        # unnecessary disk I/O on every trade-limit check.
        changed = len(recent) != len(self.trade_timestamps)
        self.trade_timestamps = recent
        if changed:
            self.save_state()
        return len(self.trade_timestamps) < self.max_trades_per_hour

    def set_active_positions_provider(self, provider: Callable[[], int]) -> None:
        """Inject a custom position-counting callable (e.g. for paper broker)."""
        self._active_positions_provider = provider

    def set_portfolio_heat_provider(self, provider: Callable[[], Optional[float]]) -> None:
        """Inject a custom portfolio heat provider (e.g. for paper broker)."""
        self._portfolio_heat_provider = provider

    def check_active_trade_limit(self, custom_count: Optional[int] = None) -> bool:
        """Enforce maximum simultaneous open positions across paper, demo, and live.

        Checks custom provider (paper broker) first, then owned magic numbers on MT5.
        Unowned manual positions do not block bot operations, but owned trades are strictly capped.
        """
        if custom_count is not None:
            return custom_count < self.max_active_positions
        if self._active_positions_provider is not None:
            try:
                count = self._active_positions_provider()
                if count is not None and isinstance(count, int):
                    return count < self.max_active_positions
            except Exception as exc:
                logging.getLogger("RiskManager").warning("active_positions_provider failed: %s", exc)
        try:
            if mt5 is not None:
                positions = mt5.positions_get()
                if positions is not None:
                    managed = getattr(self, "managed_magics", None) or {460001, 460002}
                    owned_count = sum(1 for p in positions if getattr(p, "magic", 0) in managed)
                    return owned_count < self.max_active_positions
                else:
                    logging.getLogger("RiskManager").warning("mt5.positions_get() returned None; failing closed on active position check")
                    return False
        except Exception as exc:
            logging.getLogger("RiskManager").warning("Exception in mt5.positions_get: %s; failing closed on active position check", exc)
            return False
        return True

    def register_trade(self, symbol: str):
        """
        Record a trade execution for cooldown and rate limiting.
        This should ONLY be called after broker acceptance (e.g. MT5 retcode == DONE).
        Do NOT call this at proposal time.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self.last_trade_time[symbol] = now_iso
        self.trade_timestamps.append(now_iso)
        self.save_state()

    def update_account(self, balance: float, equity: float):
        self.balance = balance
        if equity > self.peak_equity:
            self.peak_equity = equity
        self.save_state()

    def check_drawdown(self, equity: float) -> bool:
        """Return whether current equity is below the persisted max drawdown.

        This check is independent of the daily limit and is used by callers
        that have an account-equity snapshot but cannot safely query MT5 again.
        It never resets the peak on a loss.
        """
        if not self._finite(equity, positive=True) or not self._finite(self.peak_equity, positive=True):
            return False
        current = float(equity)
        peak = max(float(self.peak_equity or 0.0), current)
        if self.peak_equity <= 0:
            self.peak_equity = peak
        drawdown = (self.peak_equity - current) / self.peak_equity if self.peak_equity > 0 else 1.0
        return drawdown < float(self.max_drawdown)

    def remaining_daily_risk(self, equity: float) -> float:
        if not self._finite(equity, positive=True) or not self._finite(self.daily_start_equity, positive=True):
            return 0.0
        current_dd_amt = self.daily_start_equity - equity
        max_loss_amt = self.daily_start_equity * self.max_daily_loss
        remaining = max_loss_amt - current_dd_amt
        return float(max(0.0, remaining))

    def set_heat_budget_r(self, budget_r: float) -> None:
        """Compress/expand the portfolio heat budget (bounded 0.5R..10R)."""
        try:
            value = float(budget_r)
        except (TypeError, ValueError, OverflowError):
            return
        if math.isfinite(value) and 0.5 <= value <= 10.0:
            self.max_portfolio_heat_r = value

    def open_portfolio_heat_r(self) -> Optional[float]:
        """Measured open heat in R units; None when it cannot be measured."""
        if self._portfolio_heat_provider is not None:
            try:
                custom_heat = self._portfolio_heat_provider()
                if custom_heat is not None and math.isfinite(custom_heat):
                    return max(0.0, float(custom_heat))
                return None
            except Exception as exc:
                logging.getLogger("RiskManager").warning("portfolio_heat_provider failed: %s", exc)
                return None
        if mt5 is None:
            return None
        try:
            positions = list(mt5.positions_get() or [])
        except Exception:
            return None
        equity = self._account_equity() or self.balance
        if equity is None or equity <= 0:
            return None
        unit = equity * self.risk_per_trade
        if unit <= 0:
            return None
        if not positions:
            return 0.0
        total_loss = 0.0
        for pos in positions:
            try:
                sl = float(getattr(pos, "sl", 0.0) or 0.0)
                entry_px = float(getattr(pos, "price_open", 0.0) or 0.0)
                volume = float(getattr(pos, "volume", 0.0) or 0.0)
                dist = abs(entry_px - sl) if entry_px > 0 and sl > 0 else 0.0
                if dist <= 0:
                    continue
                spec = self._symbol_spec(pos.symbol)
                if spec and spec.trade_tick_size and spec.trade_tick_value:
                    loss_per_lot = (dist / spec.trade_tick_size) * spec.trade_tick_value
                else:
                    contract = 100000.0
                    try:
                        info = mt5.symbol_info(pos.symbol)
                        if info and getattr(info, "trade_contract_size", 0) > 0:
                            contract = float(info.trade_contract_size)
                    except Exception:
                        pass
                    loss_per_lot = dist * contract
                total_loss += loss_per_lot * max(volume, 0.01)
            except Exception:
                continue
        return total_loss / unit

    def check_portfolio_heat(self, new_trade_units_r: float = 1.0) -> bool:
        """True when adding ~1R of fresh exposure stays within the heat budget."""
        try:
            units = float(new_trade_units_r)
        except (TypeError, ValueError, OverflowError):
            return True
        heat = self.open_portfolio_heat_r()
        if heat is None:
            # Fail closed if active positions exist (paper or live) or MT5 is active
            pos_count = 0
            if self._active_positions_provider is not None:
                try:
                    pos_count = self._active_positions_provider() or 0
                except Exception:
                    pos_count = 0
            if pos_count > 0:
                logging.getLogger("RiskManager").warning(
                    "Cannot verify open portfolio heat while %d active positions exist; failing closed", pos_count
                )
                return False
            if mt5 is not None:
                try:
                    mt5_positions = mt5.positions_get()
                    if mt5_positions is not None and len(mt5_positions) > 0:
                        logging.getLogger("RiskManager").warning(
                            "Cannot verify open portfolio heat while %d MT5 positions exist; failing closed", len(mt5_positions)
                        )
                        return False
                except Exception:
                    pass
                logging.getLogger("RiskManager").warning("Cannot verify open portfolio heat while MT5 active; failing closed")
                return False
            return True
        return (heat + units) <= self.max_portfolio_heat_r

    def calc_position_size(self, symbol: str, entry: float, sl: float, confidence: float = 1.0,
                           *, atr: Optional[float] = None,
                           atr_target: Optional[float] = None,
                           alpha_composite: float = 0.0,
                           trade_direction: str = "BUY",
                           alpha_overall_ic: float = 0.0,
                           alpha_confidence: str = "unknown",
                           market: Optional[dict] = None,
                           meta_health_multiplier: Optional[float] = None) -> float:
        if (not self._finite(entry, positive=True) or not self._finite(sl, positive=True)
                or not self._finite(confidence) or float(confidence) < 0 or float(confidence) > 1):
            return 0.0
        # 1. Check Hard Limits
        if self.permanent_halt or not self.check_equity_floor():
            logging.warning("Trade Rejected: PERMANENT HALT (equity floor).")
            return 0.0
        if not self.check_daily_drawdown(): 
            logging.warning("Trade Rejected: Daily Drawdown Limit Hit.")
            return 0.0
        if not self.check_consecutive_loss_breaker():
            logging.warning(
                "Trade Rejected: Consecutive loss circuit breaker (%s/%s).",
                self.consecutive_losses, self.max_consecutive_losses,
            )
            return 0.0

        # Portfolio heat cap: refuse entries that would push aggregate open
        # risk past the R-unit budget (copula-compressible via set_heat_budget_r).
        heat_now = self.open_portfolio_heat_r()
        if heat_now is not None and (heat_now + 1.0) > self.max_portfolio_heat_r:
            logging.warning(
                "Trade Rejected: portfolio heat cap (%.2fR open / %.2fR budget).",
                heat_now, self.max_portfolio_heat_r,
            )
            return 0.0
            
        if entry == sl: return 0.0

        # 2. Strict Risk Calculation (Universal 1R with Circuit Step-Down)
        equity = self._account_equity()
        if equity is None:
            equity = self.balance
        if not self._finite(equity, positive=True):
            return 0.0
        
        if not self.check_max_drawdown(equity):
            logging.warning("Trade Rejected: Max Drawdown Limit Hit.")
            return 0.0
        self.check_weekly_circuit_breaker(equity)

        # Equity-aware scaling: drawdown ladder x anti-martingale streaks
        # x quarter-Kelly (reduction-only). Any factor reaching 0 rejects.
        dd_multiplier = self.get_drawdown_multiplier(equity)
        if dd_multiplier <= 0.0:
            logging.warning("Trade Rejected: drawdown multiplier zeroed (>=20%% DD).")
            return 0.0
        streak_multiplier = self.get_streak_multiplier()
        if streak_multiplier <= 0.0:
            logging.warning(
                "Trade Rejected: streak multiplier zeroed (%s consecutive losses).",
                self.consecutive_losses,
            )
            return 0.0
        kelly_multiplier = self.quarter_kelly_scalar()

        # Dynamic Size Scaling based on Confidence & Circuit Breaker Step-Down
        size_multiplier = 1.0
        if confidence < 0.6: size_multiplier = 0.0
        elif confidence < 0.75: size_multiplier = 0.5
        elif confidence < 0.90: size_multiplier = 1.0
        else: size_multiplier = 1.0

        circuit_multiplier = self.get_circuit_breaker_size_multiplier()

        # SPMC Performance Regime Multiplier (Tolusic 2026: 100% in Good, 25% in Bad)
        spmc_multiplier = 1.0
        if getattr(self, "spmc_filter", None) is not None:
            try:
                spmc_multiplier = float(self.spmc_filter.get_sizing_multiplier())
            except Exception:
                spmc_multiplier = 1.0

        # Opportunity cost hurdle scalar (Masoumian, 2026)
        opp_multiplier = self.opportunity_cost_scalar()
        if opp_multiplier <= 0.0:
            logging.warning("Trade Rejected: net expectancy below opportunity cost hurdle (%.2fR).", self.opportunity_cost_hurdle_r)
            return 0.0

        size_multiplier *= circuit_multiplier * dd_multiplier * streak_multiplier * kelly_multiplier * spmc_multiplier * opp_multiplier

        # --- Retail Quant Models Overlay (Document-Derived) ---
        # Integrates SPRT, CUSUM, Bayesian, Correlation, Session checks
        # into the sizing pipeline as reduction-only multipliers.
        try:
            from core.retail_quant_models import (
                CUSUMMonitor, BayesianWinRateUpdater,
                CorrelationBreakdownDetector, SessionSpreadEngine,
            )

            # CUSUM drift detection: if residual drift detected, cut 50%
            if len(self.trade_r_history) >= 20:
                cusum_mon = CUSUMMonitor(mu_0=0.0, k=0.5, threshold=4.0)
                cusum_mon.update_batch(self.trade_r_history[-30:])
                if cusum_mon.state.n_breaches > 0:
                    size_multiplier *= 0.5
                    logging.getLogger("RiskManager").info("CUSUM drift detected → 50% size reduction")

            # Bayesian win rate shrinkage
            bayesian = BayesianWinRateUpdater()
            if self.trade_r_history:
                bayesian.update_batch([r > 0 for r in self.trade_r_history[-50:]])
                bay_shrink = bayesian.get_position_shrinkage()
                if bay_shrink < 1.0:
                    size_multiplier *= bay_shrink
                    logging.getLogger("RiskManager").info(
                        "Bayesian shrinkage: %.2fx (P(edge)=%.2f)",
                        bay_shrink, bayesian.state.probability_positive_edge(),
                    )

            # Correlation breakdown: if unstable, reduce heat
            corr_detector = CorrelationBreakdownDetector(window=60, k=2.5)
            if len(self.trade_r_history) >= 10:
                # Use recent returns as proxy for correlation instability
                corr_mult = corr_detector.get_portfolio_heat_multiplier()
                if corr_mult < 1.0:
                    size_multiplier *= corr_mult
                    logging.getLogger("RiskManager").info(
                        "Correlation instability → %.0f%% heat reduction", corr_mult * 100,
                    )

        except Exception as _rqm_exc:
            logging.getLogger("RiskManager").debug("Retail quant models overlay skipped: %s", _rqm_exc)

        # Volatility targeting overlay (opt-in via atr/atr_target): scales the
        # risk fraction toward a target ATR so constant-$ risk does not let
        # realized R-volatility swell in quiet regimes (variance drag).
        if atr is not None and atr_target is not None:
            try:
                a_val, t_val = float(atr), float(atr_target)
                if math.isfinite(a_val) and a_val > 0 and math.isfinite(t_val) and t_val > 0:
                    size_multiplier *= max(0.5, min(2.0, t_val / a_val))
            except (TypeError, ValueError, OverflowError):
                pass

        # --- Alpha Ensemble Position Sizing (bypasses LLM for sizing) ---
        # Maps alpha composite score to a direction-aware multiplier.
        # Strong alpha alignment → larger size; contradiction → smaller size.
        if abs(alpha_composite) > 0.01 and trade_direction in ("BUY", "SELL"):
            try:
                from core.alpha_sizing import compute_alpha_sizing
                alpha_result = compute_alpha_sizing(
                    composite=alpha_composite,
                    trade_direction=trade_direction,
                    overall_ic=alpha_overall_ic,
                    composite_confidence=alpha_confidence,
                )
                alpha_mult = alpha_result.multiplier
                size_multiplier *= alpha_mult
                logging.getLogger("RiskManager").info(
                    "Alpha sizing: composite=%.2f dir=%s IC=%.4f → %.2fx (alignment=%s, ic_quality=%s)",
                    alpha_composite, trade_direction, alpha_overall_ic,
                    alpha_mult, alpha_result.alignment, alpha_result.ic_quality,
                )
            except Exception as alpha_exc:
                logging.getLogger("RiskManager").debug("Alpha sizing skipped: %s", alpha_exc)

        # --- Alpha Mining Risk Overlay (Phase 6) ---
        # Integrates alpha risk profile from mined alpha strategies
        try:
            from research.alpha_miner.alpha_risk import AlphaRiskManager
            alpha_risk_mgr = AlphaRiskManager()
            
            # Check for alpha mining risk profile in market context
            alpha_risk_profile = market.get("alpha_risk_profile") if market else None
            if alpha_risk_profile is None:
                # Build profile from available alpha metrics
                if alpha_overall_ic > 0:
                    profile = alpha_risk_mgr.assess_alpha_risk(
                        alpha_id=market.get("alpha_id", "unknown") if market else "unknown",
                        ic_metrics={
                            "ic_mean": alpha_overall_ic,
                            "ic_quality": alpha_confidence,
                        },
                        backtest_results=market.get("alpha_backtest", {}) if market else {},
                    )
                    alpha_risk_mult = profile.sizing_multiplier
                    size_multiplier *= alpha_risk_mult
                    logging.getLogger("RiskManager").info(
                        "Alpha mining risk overlay: IC=%.4f quality=%s → %.2fx",
                        alpha_overall_ic, alpha_confidence, alpha_risk_mult,
                    )
        except Exception as _alpha_risk_exc:
            logging.getLogger("RiskManager").debug("Alpha mining risk overlay skipped: %s", _alpha_risk_exc)

        # --- Kronos Risk Adjustment (UAQP P0) ---
        # Kronos forecast provides a risk_adjustment_factor 0.5-1.0
        # that scales position size based on forecast confidence.
        market = market or {}
        kronos_risk_adj = float(market.get("kronos_risk_adjustment_factor", 1.0))
        if 0.0 < kronos_risk_adj < 1.0:
            size_multiplier *= kronos_risk_adj
            logging.getLogger("RiskManager").info(
                "Kronos risk adjustment: %.2fx (forecast confidence)", kronos_risk_adj,
            )

        # --- Walk-Forward Decay Gate (UAQP P0) ---
        wf_status = str(market.get("walk_forward_status", "UNKNOWN"))
        if wf_status == "DEAD":
            size_multiplier = 0.0
            logging.getLogger("RiskManager").warning(
                "Walk-forward DEAD → zero position size (alpha is harmful)",
            )
        elif wf_status == "DECAYING":
            size_multiplier *= 0.5
            logging.getLogger("RiskManager").info(
                "Walk-forward DECAYING → 50%% size reduction",
            )

        # --- Walk-Forward Efficiency sizing compression ---
        # WFE = mean(OOS Sharpe)/mean(IS Sharpe). OOS-unprofitable → no size;
        # weak-but-positive WFE → proportional haircut capped at 50% reduction.
        wfe_raw = market.get("walk_forward_wfe")
        if isinstance(wfe_raw, dict) and int(wfe_raw.get("n_folds", 0)) >= 2:
            try:
                _wfe = float(wfe_raw.get("wfe", 0.0))
                _mean_test = float(wfe_raw.get("mean_test_sharpe", 0.0))
                if _mean_test <= 0.0:
                    size_multiplier = 0.0
                    logging.getLogger("RiskManager").warning(
                        "Walk-forward WFE: OOS unprofitable (Sharpe %.4f) → zero size",
                        _mean_test,
                    )
                elif _wfe < 0.5:
                    # Keep 50-100% of size proportional to how much edge survives.
                    size_multiplier *= max(0.5, _wfe)
                    logging.getLogger("RiskManager").info(
                        "Walk-forward WFE %.3f < 0.5 → size scaled to %.0f%%",
                        _wfe, max(0.5, _wfe) * 100,
                    )
            except (TypeError, ValueError):
                pass

        # --- Overfit Gate (UAQP P0) ---
        overfit_level = str(market.get("overfit_risk_level", "LOW"))
        if overfit_level == "CRITICAL":
            size_multiplier = 0.0
            logging.getLogger("RiskManager").warning(
                "Overfit CRITICAL → zero position size",
            )
        elif overfit_level == "HIGH":
            size_multiplier *= 0.25
            logging.getLogger("RiskManager").info(
                "Overfit HIGH → 75%% size reduction",
            )

        # --- Data-Drift sizing compression (PSI/KS distribution shift) ---
        # Major shift → half size (models trained on the old distribution are
        # untrustworthy); advisory SHIFT leaves size untouched.
        drift_status = str(market.get("data_drift_status", "") or "")
        if drift_status == "DRIFT":
            size_multiplier *= 0.5
            logging.getLogger("RiskManager").warning(
                "Data drift DRIFT → 50%% size reduction (feature distributions in major shift)",
            )
        # --- CVaR-95 Tail Risk Sizing Compression (Parametric Institutional Risk) ---
        try:
            if len(self.trade_r_history) >= 15:
                cvar_stats = self.calculate_parametric_cvar(confidence_level=0.95, min_samples=15)
                if cvar_stats.get("available") and cvar_stats.get("cvar_r", 0.0) > 2.0:
                    cvar_val = float(cvar_stats["cvar_r"])
                    cvar_scalar = max(0.20, min(1.0, 2.0 / cvar_val))
                    size_multiplier *= cvar_scalar
                    logging.getLogger("RiskManager").info(
                        "CVaR-95 tail risk compression: %.2fR → %.2fx scalar",
                        cvar_val, cvar_scalar,
                    )
        except Exception as _cvar_exc:
            logging.getLogger("RiskManager").debug("CVaR sizing overlay skipped: %s", _cvar_exc)

        # --- Meta-Reflector Continuous Self-Diagnosis Multiplier ---
        if meta_health_multiplier is not None:
            try:
                m_mult = float(meta_health_multiplier)
                if math.isfinite(m_mult):
                    clamped_m = max(0.10, min(1.0, m_mult))
                    size_multiplier *= clamped_m
                    logging.getLogger("RiskManager").info(
                        "Meta health multiplier applied: %.2fx", clamped_m,
                    )
            except (TypeError, ValueError):
                pass
        elif market and "meta_health_multiplier" in market:
            try:
                m_mult = float(market["meta_health_multiplier"])
                if math.isfinite(m_mult):
                    clamped_m = max(0.10, min(1.0, m_mult))
                    size_multiplier *= clamped_m
                    logging.getLogger("RiskManager").info(
                        "Meta health multiplier (from market context) applied: %.2fx", clamped_m,
                    )
            except (TypeError, ValueError):
                pass

        # Use configured risk per trade (e.g. 0.01) * Multiplier
        risk_amt = equity * self.risk_per_trade * size_multiplier
        # Only log if > 0 to avoid spam on rejections
        if risk_amt > 0:
             logging.getLogger("RiskManager").info(
                 f"Risk Logic: Base {self.risk_per_trade:.1%} * Conf {confidence:.2f} * Circuit {circuit_multiplier:.2f} "
                 f"* DD {dd_multiplier:.2f} * Streak {streak_multiplier:.2f} * Kelly {kelly_multiplier:.2f} (x{size_multiplier}) = Risk ${risk_amt:.2f}"
             )
        
        # 3. Dynamic Cap based on Daily Limit
        remaining_daily_risk = self.remaining_daily_risk(equity)
        
        if risk_amt > remaining_daily_risk:
            logging.warning(f"Risk {risk_amt:.2f} capped to {remaining_daily_risk:.2f} (Daily Limit)")
            risk_amt = max(0.0, remaining_daily_risk)
            
        if risk_amt <= 0.01: 
            return 0.0

        # 4. Volume Calculation
        dist = abs(entry - sl)
        info = self._symbol_spec(symbol)
        
        if not info:
            # Fallback (Forex)
            point_size = 0.001 if 'JPY' in symbol else 0.00001
            loss_per_lot = (dist / point_size) * 1.0 # Standard
            min_vol, max_vol, step = 0.01, 100.0, 0.01
        else:
            tick_size = info.trade_tick_size
            tick_value = info.trade_tick_value
            min_vol = info.volume_min
            max_vol = info.volume_max
            step = info.volume_step
            if (not self._finite(tick_size, positive=True) or not self._finite(tick_value, positive=True)
                    or not self._finite(min_vol, positive=True) or not self._finite(max_vol, positive=True)
                    or not self._finite(step, positive=True) or float(max_vol) < float(min_vol)):
                return 0.0
            
            # Crypto/Index handling (TickValue often 0 or 1 per point)
            if tick_size == 0 or tick_value == 0:
                 logging.warning(f"Rejecting trade on {symbol}: tick_size or tick_value is 0.")
                 return 0.0
            else:
                 loss_per_lot = (dist / tick_size) * tick_value
        
        if not self._finite(loss_per_lot, positive=True): return 0.0
        
        volume = risk_amt / loss_per_lot
        
        # 5. Normalize & Clip
        if step > 0:
            # Avoid binary floating-point truncation (0.10 / 0.01 can be
            # represented as 9.999999...), which would silently undersize a
            # correctly calculated risk amount.
            volume = math.floor((volume / step) + 1e-9) * step
            
        if not self._finite(volume, positive=True) or volume < float(min_vol):
            # Rounding up to the broker minimum would exceed the requested
            # dollar risk, so refuse the trade instead of over-risking it.
            return 0.0
        volume = float(min(volume, max_vol))
        return float(f"{volume:.2f}")

