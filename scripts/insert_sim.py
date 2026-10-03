import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with open("core/self_improvement_loop.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

insert_idx = None
for i, line in enumerate(lines):
    if "# Experience Memory (FactorMiner-style)" in line and i > 150:
        insert_idx = i
        break

if insert_idx is None:
    print("Insertion point not found!")
    sys.exit(1)

print(f"Inserting before line {insert_idx + 1}")

new_class = """
# ---------------------------------------------------------------------------
# Empirical Simulator
# ---------------------------------------------------------------------------

@dataclass
class _Trade:
    pnl: float
    r_multiple: float
    exit_reason: str


class EmpiricalSimulator:
    def __init__(self, db_path="trading_state.db", max_trades=500):
        self.db_path = db_path
        self.max_trades = max_trades
        self.trades = []
        self._load_trades()

    def _load_trades(self):
        try:
            import sqlite3
            con = sqlite3.connect(self.db_path)
            rows = con.execute(
                "SELECT pnl, risk_amount, exit_reason FROM trades "
                "WHERE pnl IS NOT NULL AND closed_at IS NOT NULL "
                "ORDER BY rowid DESC LIMIT ?",
                (self.max_trades,),
            ).fetchall()
            con.close()
            for pnl, risk_amount, exit_reason in rows:
                r = pnl / risk_amount if risk_amount and risk_amount != 0 else 0.0
                self.trades.append(_Trade(pnl=pnl, r_multiple=r, exit_reason=exit_reason or ""))
        except Exception as e:
            logger.debug("Failed to load trades for simulator: %s", e)

    @property
    def n(self):
        return len(self.trades)

    def baseline_metrics(self):
        return self._metrics_for(self.trades)

    def simulate(self, proposal):
        if not self.trades:
            return None
        changes = proposal.parameter_changes
        category = proposal.category
        if "entry_confidence_threshold" in changes:
            return None
        if "risk_per_trade" in changes:
            proposed_val = self._extract_float(changes["risk_per_trade"])
            if proposed_val is None:
                return None
            risk_amounts = [t.pnl / t.r_multiple if t.r_multiple != 0 else 0 for t in self.trades]
            median_risk = float(np.median(risk_amounts)) if risk_amounts else 0.01
            if median_risk == 0:
                return None
            ratio = proposed_val / median_risk
            scaled = [_Trade(pnl=t.pnl * ratio, r_multiple=t.r_multiple * ratio,
                             exit_reason=t.exit_reason) for t in self.trades]
            return self._metrics_for(scaled)
        if "position_scale" in changes:
            proposed_val = self._extract_float(changes["position_scale"])
            if proposed_val is None:
                return None
            scaled = [_Trade(pnl=t.pnl * proposed_val, r_multiple=t.r_multiple * proposed_val,
                             exit_reason=t.exit_reason) for t in self.trades]
            return self._metrics_for(scaled)
        if category == "risk_params":
            for v in changes.values():
                val = self._extract_float(v)
                if val is not None:
                    risk_amounts = [t.pnl / t.r_multiple if t.r_multiple != 0 else 0 for t in self.trades]
                    median_risk = float(np.median(risk_amounts)) if risk_amounts else 0.01
                    if median_risk == 0:
                        return None
                    ratio = val / median_risk
                    scaled = [_Trade(pnl=t.pnl * ratio, r_multiple=t.r_multiple * ratio,
                                     exit_reason=t.exit_reason) for t in self.trades]
                    return self._metrics_for(scaled)
        return None

    @staticmethod
    def _extract_float(v):
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for key in ("proposed", "value", "new"):
                if key in v:
                    return EmpiricalSimulator._extract_float(v[key])
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return None
        return None

    def _metrics_for(self, trades):
        n = len(trades)
        if n == 0:
            return {"win_rate": 0.0, "avg_r_multiple": 0.0, "sharpe_rolling": 0.0,
                    "current_drawdown": 0.0, "total_trades": 0, "expectancy": 0.0}
        pnls = np.array([t.pnl for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        win_rate = len(wins) / n if n else 0.0
        avg_win = float(np.mean(wins)) if len(wins) else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) else 0.0
        expectancy = float(np.mean(pnls))
        avg_r = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0
        std = float(np.std(pnls, ddof=1)) if n > 1 else 0.0
        sharpe = (expectancy / std * np.sqrt(252)) if std > 0 else 0.0
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        drawdown = cum - peak
        max_dd = float(np.min(drawdown)) if len(drawdown) else 0.0
        return {"win_rate": win_rate, "avg_r_multiple": avg_r, "sharpe_rolling": sharpe,
                "current_drawdown": max_dd, "total_trades": n, "expectancy": expectancy}


"""

new_lines = lines[:insert_idx] + [new_class] + lines[insert_idx:]

with open("core/self_improvement_loop.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)

print("Done")
