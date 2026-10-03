"""Local rolling return correlations for deterministic portfolio risk control."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import sqlite3
import numpy as np
from typing import Any, Dict, Iterable, List, Optional, Tuple

from trading_safety import exposure_buckets


class CorrelationRisk:
    def __init__(self, db_path: str = "trading_state.db", *, minimum_samples: int = 30,
                 threshold: float = 0.70, lookback_bars: int = 250, max_staleness_hours: float = 48.0):
        if (not isinstance(minimum_samples, int) or isinstance(minimum_samples, bool)
                or minimum_samples < 2):
            raise ValueError("minimum_samples must be an integer >= 2")
        if (not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
                or not math.isfinite(float(threshold)) or not 0.0 <= float(threshold) <= 1.0):
            raise ValueError("threshold must be finite and within [0, 1]")
        if (not isinstance(lookback_bars, int) or isinstance(lookback_bars, bool)
                or lookback_bars < minimum_samples):
            raise ValueError("lookback_bars must be an integer >= minimum_samples")
        if (not isinstance(max_staleness_hours, (int, float)) or isinstance(max_staleness_hours, bool)
                or not math.isfinite(float(max_staleness_hours)) or float(max_staleness_hours) <= 0):
            raise ValueError("max_staleness_hours must be finite and positive")
        self.db_path, self.minimum_samples, self.threshold = db_path, minimum_samples, float(threshold)
        self.lookback_bars = lookback_bars
        self.max_staleness_hours = float(max_staleness_hours)
        with self._con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS correlation_returns (
              symbol TEXT NOT NULL, timeframe TEXT NOT NULL, bar_time TEXT NOT NULL,
              return_fraction REAL NOT NULL, captured_at TEXT NOT NULL,
              PRIMARY KEY(symbol,timeframe,bar_time)
            );
            CREATE INDEX IF NOT EXISTS idx_correlation_returns_lookup
              ON correlation_returns(symbol,timeframe,bar_time);
            """)

    def _con(self):
        con = sqlite3.connect(self.db_path); con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _bar_time(bar: Dict[str, Any]) -> str:
        value = bar.get("timestamp", bar.get("time", bar.get("date", "")))
        return str(value or "")

    def record_completed_bars(self, symbol: str, timeframe: str, bars: Iterable[Dict[str, Any]]) -> int:
        """Store only completed, timestamped returns; no forming candle is used."""
        items = [bar for bar in bars if isinstance(bar, dict)]
        rows = []
        for previous, current in zip(items, items[1:]):
            try:
                previous_close, close = float(previous["close"]), float(current["close"])
                bar_time = self._bar_time(current)
                if previous_close <= 0 or close <= 0 or not bar_time:
                    continue
                rows.append((symbol, timeframe, bar_time, math.log(close / previous_close), datetime.now(timezone.utc).isoformat()))
            except (KeyError, TypeError, ValueError):
                continue
        if rows:
            with self._con() as con:
                con.executemany("INSERT OR REPLACE INTO correlation_returns(symbol,timeframe,bar_time,return_fraction,captured_at) VALUES(?,?,?,?,?)", rows)
        return len(rows)

    def _correlation(self, symbol_a: str, symbol_b: str, timeframe: str) -> Dict[str, Any] | None:
        with self._con() as con:
            rows = con.execute("""SELECT a.return_fraction a,b.return_fraction b, a.bar_time bar_time
                                FROM correlation_returns a JOIN correlation_returns b ON a.bar_time=b.bar_time AND a.timeframe=b.timeframe
                                WHERE a.symbol=? AND b.symbol=? AND a.timeframe=?
                                ORDER BY a.bar_time DESC LIMIT ?""", (symbol_a, symbol_b, timeframe, self.lookback_bars)).fetchall()
        if len(rows) < self.minimum_samples:
            return None
        left, right = [float(row["a"]) for row in rows], [float(row["b"]) for row in rows]
        left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
        covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
        left_dev = math.sqrt(sum((a - left_mean) ** 2 for a in left))
        right_dev = math.sqrt(sum((b - right_mean) ** 2 for b in right))
        if left_dev == 0 or right_dev == 0:
            return None
            
        samples = len(rows)
        latest_time_str = rows[0]["bar_time"]
        try:
            if str(latest_time_str).isdigit():
                latest_dt = datetime.fromtimestamp(int(latest_time_str), timezone.utc)
            else:
                s = str(latest_time_str).replace('Z', '+00:00')
                latest_dt = datetime.fromisoformat(s)
                if latest_dt.tzinfo is None:
                    latest_dt = latest_dt.replace(tzinfo=timezone.utc)
            staleness_hours = (datetime.now(timezone.utc) - latest_dt).total_seconds() / 3600.0
        except (TypeError, ValueError, OverflowError, OSError):
            # An unparseable bar time is not evidence of a fresh correlation.
            # Force the conservative driver-bucket fallback instead of
            # allowing malformed data to influence portfolio permission.
            staleness_hours = float("inf")

        is_stale = staleness_hours > self.max_staleness_hours
        confidence = "LOW_CONFIDENCE" if samples < 2 * self.minimum_samples else "HIGH_CONFIDENCE"
        
        return {
            "raw_correlation": max(-1.0, min(1.0, covariance / (left_dev * right_dev))), 
            "samples": samples,
            "confidence": confidence,
            "stale": is_stale
        }

    @staticmethod
    def _parse_bar_datetime(value: Any) -> datetime | None:
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return datetime.fromtimestamp(float(value), timezone.utc)
            text = str(value or "").strip()
            if text and text.replace(".", "", 1).isdigit():
                return datetime.fromtimestamp(float(text), timezone.utc)
            text = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def point_in_time_observations(self, timeframe: str, *, as_of: Any,
                                   exclude_symbol: str = "") -> Dict[str, Dict[str, Any]]:
        """Return latest non-stale cross-market returns known at ``as_of``.

        This is context for replay/features only.  It never reads rows after
        the completed-bar cutoff and it does not grant correlation-risk
        permission; the arbiter continues to calculate its own assessment.
        """
        cutoff = self._parse_bar_datetime(as_of)
        if cutoff is None:
            return {}
        with self._con() as con:
            rows = con.execute(
                "SELECT symbol,bar_time,return_fraction FROM correlation_returns "
                "WHERE timeframe=? AND symbol<>? ORDER BY bar_time DESC LIMIT ?",
                (timeframe, str(exclude_symbol), self.lookback_bars * 20),
            ).fetchall()
        latest: Dict[str, Dict[str, Any]] = {}
        max_age = timedelta(hours=self.max_staleness_hours)
        for row in rows:
            symbol = str(row["symbol"] or "")
            bar_time = str(row["bar_time"] or "")
            observed = self._parse_bar_datetime(bar_time)
            if not symbol or observed is None or observed > cutoff or cutoff - observed > max_age:
                continue
            try:
                value = float(row["return_fraction"])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            previous = latest.get(symbol)
            if previous is not None and observed <= self._parse_bar_datetime(previous["timestamp"]):
                continue
            latest[symbol] = {"timestamp": observed.isoformat(), "return_fraction": value}
        return latest

    def correlated_open_risk(self, store: Any, symbol: str, side: str, timeframe: str) -> Dict[str, Any]:
        """Estimate same-direction open risk; conservative bucket fallback on sparse data."""
        side_factor = 1 if side.upper() == "BUY" else -1
        total, pairs = 0.0, []
        for row in store.open_trade_rows():
            existing_symbol, existing_side = str(row["symbol"]), str(row["side"])
            estimated = self._correlation(symbol, existing_symbol, timeframe)
            
            if estimated and not estimated.get("stale", False):
                effective = estimated["raw_correlation"] * side_factor * (1 if existing_side.upper() == "BUY" else -1)
                source = "rolling_return_correlation"
                samples = estimated["samples"]
                if estimated["confidence"] == "LOW_CONFIDENCE":
                    source = "rolling_return_correlation (LOW_CONFIDENCE)"
                    current_threshold = 0.5
                else:
                    current_threshold = self.threshold
            else:
                overlap = exposure_buckets(symbol, side) & exposure_buckets(existing_symbol, existing_side)
                effective, source, samples = (1.0 if overlap else 0.0), "conservative_driver_bucket", 0
                if estimated and estimated.get("stale", False):
                    source = "conservative_driver_bucket (stale fallback)"
                current_threshold = self.threshold

            related = effective >= current_threshold
            risk = float(row["risk_amount"] or 0)
            if related:
                total += risk
            pairs.append({"ticket": int(row["ticket"]), "symbol": existing_symbol, "side": existing_side,
                          "effective_correlation": effective, "related": related, "risk_amount": risk,
                          "source": source, "samples": samples, "threshold_used": current_threshold})
        return {"correlated_open_risk": total, "threshold": self.threshold, "pairs": pairs}

    def portfolio_correlation_summary(self, store: Any, timeframe: str) -> Dict[str, Any]:
        """Matrix/dict of all open positions' pairwise correlations and total correlated risk."""
        open_trades = list(store.open_trade_rows())
        matrix = {}
        correlated_tickets = set()
        
        for i, row_a in enumerate(open_trades):
            sym_a, side_a = str(row_a["symbol"]), str(row_a["side"])
            ticket_a, risk_a = int(row_a["ticket"]), float(row_a["risk_amount"] or 0)
            
            matrix[ticket_a] = {
                "symbol": sym_a, "side": side_a, "risk": risk_a, "correlations": {}
            }
            
            for j, row_b in enumerate(open_trades):
                if i == j: continue
                sym_b, side_b = str(row_b["symbol"]), str(row_b["side"])
                ticket_b = int(row_b["ticket"])
                
                est = self._correlation(sym_a, sym_b, timeframe)
                if est and not est.get("stale", False):
                    eff = est["raw_correlation"] * (1 if side_a.upper() == "BUY" else -1) * (1 if side_b.upper() == "BUY" else -1)
                    thresh = 0.5 if est["confidence"] == "LOW_CONFIDENCE" else self.threshold
                else:
                    overlap = exposure_buckets(sym_a, side_a) & exposure_buckets(sym_b, side_b)
                    eff = 1.0 if overlap else 0.0
                    thresh = self.threshold
                    
                related = eff >= thresh
                matrix[ticket_a]["correlations"][ticket_b] = {"effective": eff, "related": related}
                if related:
                    correlated_tickets.add(ticket_a)
                    correlated_tickets.add(ticket_b)
                    
        total_risk = sum(float(row["risk_amount"] or 0) for row in open_trades if int(row["ticket"]) in correlated_tickets)
        return {"matrix": matrix, "total_correlated_risk": total_risk}

    def known_symbols(self, timeframe: str) -> List[str]:
        """Symbols that already have stored completed-bar returns for ``timeframe``."""
        with self._con() as con:
            rows = con.execute(
                "SELECT DISTINCT symbol FROM correlation_returns WHERE timeframe=? ORDER BY symbol",
                (str(timeframe),),
            ).fetchall()
        return [str(row["symbol"]) for row in rows if row["symbol"]]

    def get_multi_asset_returns_matrix(
        self,
        timeframe: str,
        *,
        lookback: int = 60,
        min_assets: int = 2,
    ) -> Optional[Tuple[np.ndarray, List[str]]]:
        """Return an aligned (n_samples, n_assets) returns matrix for copula analysis.

        Fetches stored completed-bar returns for all known symbols at the given
        timeframe, aligns them by ``bar_time``, and returns the dense matrix
        together with the column symbol list.

        Returns ``None`` if fewer than ``min_assets`` symbols have enough
        overlapping data (>= ``lookback`` aligned samples).
        """
        symbols = self.known_symbols(timeframe)
        if len(symbols) < min_assets:
            return None

        # Fetch returns for each symbol, keyed by bar_time
        symbol_returns: Dict[str, Dict[str, float]] = {}
        with self._con() as con:
            for sym in symbols:
                rows = con.execute(
                    "SELECT bar_time, return_fraction FROM correlation_returns "
                    "WHERE symbol=? AND timeframe=? ORDER BY bar_time DESC LIMIT ?",
                    (sym, timeframe, lookback),
                ).fetchall()
                if rows:
                    symbol_returns[sym] = {
                        str(row["bar_time"]): float(row["return_fraction"])
                        for row in rows
                        if math.isfinite(float(row["return_fraction"]))
                    }

        if len(symbol_returns) < min_assets:
            return None

        # Find bar_times present in ALL assets (inner join alignment)
        all_times = sorted(
            set.intersection(*(set(rets.keys()) for rets in symbol_returns.values()))
        )
        if len(all_times) < lookback:
            return None

        # Take the most recent ``lookback`` aligned samples
        recent_times = all_times[-lookback:]
        active_symbols = [
            s for s in symbols
            if s in symbol_returns and len(symbol_returns[s]) >= lookback
        ]
        if len(active_symbols) < min_assets:
            active_symbols = symbols[:min_assets]  # fallback: take first N

        matrix = np.array(
            [[symbol_returns[s][t] for s in active_symbols] for t in recent_times],
            dtype=float,
        )
        return matrix, active_symbols

    def pair_correlations(
        self,
        symbols: Iterable[str],
        timeframe: str,
        *,
        allow_stale: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Return non-permission pairwise correlations for research / regime context.

        Keys are ``SYM_A|SYM_B`` (sorted).  Missing/stale pairs are omitted unless
        ``allow_stale`` is True (still marked ``stale: True``).
        """
        ordered = sorted({str(sym).upper() for sym in symbols if str(sym or "").strip()})
        out: Dict[str, Dict[str, Any]] = {}
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                estimated = self._correlation(left, right, timeframe)
                if estimated is None:
                    continue
                if estimated.get("stale") and not allow_stale:
                    continue
                key = f"{left}|{right}"
                out[key] = {
                    "raw_correlation": float(estimated["raw_correlation"]),
                    "samples": int(estimated["samples"]),
                    "confidence": estimated.get("confidence"),
                    "stale": bool(estimated.get("stale")),
                    "execution_authority": False,
                }
        return out

    def purge_old_returns(self, days: int = 90) -> int:
        """Removes correlation returns older than `days`."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._con() as con:
            cursor = con.execute("DELETE FROM correlation_returns WHERE captured_at < ?", (cutoff,))
            con.commit()
            return cursor.rowcount
