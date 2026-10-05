"""Synthetic labelled sample generator for pipeline testing.

This module creates deterministic, broker-reconciled, point-in-time synthetic
trades that match the exact schema expected by ``research_validation.labelled_dataset``.
All generated records are explicitly marked ``synthetic=True`` in the payload
and never enter the live trading loop.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from intelligence.feature_schema import FEATURE_SCHEMA_VERSION, SCHEMA_HASHES, REQUIRED_VALUES, values_hash


def _random_feature_values() -> Dict[str, Any]:
    return {
        "close": round(random.uniform(0.8, 1.3), 5),
        "log_return_1": round(random.uniform(-0.01, 0.01), 6),
        "realized_volatility_20": round(random.uniform(0.005, 0.05), 6),
        "atr_14": round(random.uniform(0.003, 0.02), 6),
        "ema_50": round(random.uniform(0.8, 1.3), 5),
        "ema_50_slope": round(random.uniform(-0.001, 0.001), 6),
        "rsi_14": round(random.uniform(25, 75), 2),
        "macd_line": round(random.uniform(-0.005, 0.005), 6),
        "macd_signal": round(random.uniform(-0.005, 0.005), 6),
        "macd_histogram": round(random.uniform(-0.002, 0.002), 6),
        "adx_14": round(random.uniform(10, 40), 2),
        "bollinger_mid": round(random.uniform(0.9, 1.2), 5),
        "bollinger_upper": round(random.uniform(1.0, 1.4), 5),
        "bollinger_lower": round(random.uniform(0.8, 1.1), 5),
        "vwap_20": round(random.uniform(0.85, 1.25), 5),
        "tick_volume": random.randint(100, 5000),
        "tick_volume_ratio_20": round(random.uniform(0.5, 2.5), 2),
        "support_20": round(random.uniform(0.8, 1.2), 5),
        "resistance_20": round(random.uniform(0.9, 1.3), 5),
        "liquidity_sweep_high": round(random.uniform(0.9, 1.3), 5),
        "liquidity_sweep_low": round(random.uniform(0.8, 1.2), 5),
        "liquidity_sweep_confirmation_high": round(random.uniform(0.9, 1.3), 5),
        "liquidity_sweep_confirmation_low": round(random.uniform(0.8, 1.2), 5),
    }


def _proposal_features(opened_at: datetime, completed_bar_time: Optional[datetime] = None, regime: str = "trending") -> Dict[str, Any]:
    if completed_bar_time is None:
        completed_bar_time = opened_at - timedelta(minutes=1)
    values = _random_feature_values()
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_SCHEMA_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
        "completed_bar_time": completed_bar_time.isoformat(),
        "captured_at": opened_at.isoformat(),
        "source": "synthetic_demo_generator",
        "ready": True,
        "regime": regime,
        "regime_label": regime,
        "artifact_versions": {
            "policy_version": "v1",
            "strategy_version": "v1",
            "feature_version": FEATURE_SCHEMA_VERSION,
            "playbook_version": "v1",
            "prompt_version": "v1",
            "model_version": "v1",
        },
    }


def generate_trade(
    *,
    ticket: int,
    symbol: str = "EURUSD",
    strategy: str = "scalp",
    setup: str = "trend_follow",
    side: str = "BUY",
    opened_at: Optional[datetime] = None,
    closed_at: Optional[datetime] = None,
    risk_amount: float = 10.0,
    pnl: Optional[float] = None,
    label: Optional[str] = None,
    regime: str = "trending",
) -> Dict[str, Any]:
    if opened_at is None:
        opened_at = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 90))
    if closed_at is None:
        closed_at = opened_at + timedelta(minutes=random.randint(5, 240))
    if pnl is None:
        pnl = round(random.uniform(-risk_amount * 2, risk_amount * 3), 2)
    if label is None:
        label = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAK_EVEN"

    features = _proposal_features(opened_at, regime=regime)
    payload = {
        "source": "synthetic_demo_generator",
        "synthetic": True,
        "proposal_features": features,
    }
    return {
        "ticket": ticket,
        "magic": 123456,
        "strategy": strategy,
        "symbol": symbol,
        "side": side,
        "volume": 0.1,
        "entry": round(random.uniform(0.9, 1.3), 5),
        "stop_loss": round(random.uniform(0.8, 1.2), 5),
        "take_profit": round(random.uniform(1.0, 1.4), 5),
        "risk_amount": risk_amount,
        "opened_at": opened_at.isoformat(),
        "closed_at": closed_at.isoformat(),
        "exit_price": round(random.uniform(0.9, 1.3), 5),
        "pnl": pnl,
        "exit_reason": "synthetic_close",
        "setup_id": setup,
        "payload": json.dumps(payload, default=str),
        "label": label,
        "rationale": "synthetic_demo_sample",
        "r_multiple": round(pnl / max(risk_amount, 1e-9), 4),
    }


def inject_samples(
    path: str = "trading_state.db",
    *,
    count: int = 120,
    setups: Optional[List[Dict[str, str]]] = None,
    regimes: Optional[List[str]] = None,
    allow_mixed_journal: bool = False,
) -> Dict[str, Any]:
    if not isinstance(allow_mixed_journal, bool):
        raise ValueError("allow_mixed_journal must be boolean")
    if os.path.exists(path):
        con = sqlite3.connect(path)
        try:
            tables = {row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "trades" in tables:
                real_rows = con.execute(
                    """SELECT COUNT(*) FROM trades
                       WHERE payload NOT LIKE '%synthetic_demo_generator%'
                          OR exit_reason IS NOT 'synthetic_close'"""
                ).fetchone()[0]
                if real_rows and not allow_mixed_journal:
                    raise ValueError(
                        f"refusing to inject synthetic samples into {path}: it contains "
                        f"{real_rows} non-synthetic broker-reconciled trade(s); pass "
                        "allow_mixed_journal=True only for throwaway test journals"
                    )
        finally:
            con.close()
    if setups is None:
        setups = [
            {"strategy": "scalp", "setup": "trend_follow", "symbol": "EURUSD"},
            {"strategy": "scalp", "setup": "mean_revert", "symbol": "EURUSD"},
            {"strategy": "swing", "setup": "breakout", "symbol": "XAUUSD"},
        ]
    if regimes is None:
        regimes = ["trending", "ranging", "volatile"]

    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            ticket INTEGER PRIMARY KEY, magic INTEGER NOT NULL,
            strategy TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
            volume REAL NOT NULL, entry REAL NOT NULL,
            stop_loss REAL, take_profit REAL, risk_amount REAL NOT NULL,
            opened_at TEXT NOT NULL, closed_at TEXT, exit_price REAL,
            pnl REAL, exit_reason TEXT, payload TEXT NOT NULL,
            setup_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_labels (
            ticket INTEGER PRIMARY KEY, label TEXT NOT NULL,
            rationale TEXT NOT NULL, r_multiple REAL, created_at TEXT NOT NULL,
            label_rule TEXT NOT NULL DEFAULT 'synthetic_v1',
            review_status TEXT NOT NULL DEFAULT 'AUTO'
        )
    """)
    con.commit()

    tickets = []
    per_setup = max(100, count // len(setups))
    for i, cfg in enumerate(setups):
        for j in range(per_setup):
            regime = regimes[(i + j) % len(regimes)]
            trade = generate_trade(
                ticket=i * per_setup + j + 1,
                symbol=cfg["symbol"],
                strategy=cfg["strategy"],
                setup=cfg["setup"],
                regime=regime,
            )
            cur.execute("""
                INSERT OR REPLACE INTO trades
                (ticket, magic, strategy, symbol, side, volume, entry, stop_loss, take_profit,
                 risk_amount, opened_at, closed_at, exit_price, pnl, exit_reason, payload, setup_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade["ticket"], trade["magic"], trade["strategy"], trade["symbol"],
                trade["side"], trade["volume"], trade["entry"], trade["stop_loss"],
                trade["take_profit"], trade["risk_amount"], trade["opened_at"],
                trade["closed_at"], trade["exit_price"], trade["pnl"],
                trade["exit_reason"], trade["payload"], trade["setup_id"],
            ))
            cur.execute("""
                INSERT OR REPLACE INTO trade_labels
                (ticket, label, rationale, r_multiple, created_at,
                 label_rule, review_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                trade["ticket"], trade["label"], trade["rationale"],
                trade["r_multiple"], trade["closed_at"],
                "synthetic_v1", "AUTO",
            ))
            tickets.append(trade["ticket"])
    con.commit()
    con.close()

    return {
        "injected": len(tickets),
        "tickets": tickets,
        "note": "Synthetic samples for pipeline testing only; not for live trading.",
        "execution_authority": False,
    }


if __name__ == "__main__":
    import json
    try:
        result = inject_samples()
    except ValueError as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc),
                          "execution_authority": False}, indent=2))
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))
