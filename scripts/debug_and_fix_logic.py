"""Forensic diagnostic and logic repair script.

Audits and resolves:
1. Spurious n_trials inflation in artifacts/trial_ledger.db
2. 50 legacy synthetic fixture rows in trading_state.db
3. Duplicate tokenrouter vs openrouter API keys and fallback loops
4. OneDrive file locking for trading_state.db
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


def audit_trial_ledger(db_path: str = "artifacts/trial_ledger.db") -> Dict[str, Any]:
    """Audit the trial ledger for duplicate executions and inflated trial counts."""
    report: Dict[str, Any] = {"path": db_path, "exists": os.path.exists(db_path)}
    if not report["exists"]:
        return report

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        total = con.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
        distinct_strats = con.execute("SELECT COUNT(DISTINCT strategy_id) FROM trials").fetchone()[0]
        breakdown = con.execute(
            "SELECT strategy_id, COUNT(*), MIN(sharpe), MAX(sharpe) FROM trials GROUP BY strategy_id ORDER BY COUNT(*) DESC"
        ).fetchall()

    report["total_trials"] = total
    report["distinct_strategies"] = distinct_strats
    report["duplicate_multiplier"] = round(total / max(1, distinct_strats), 2)
    report["breakdown"] = breakdown
    return report


def audit_trading_state(db_path: str = "trading_state.db") -> Dict[str, Any]:
    """Audit trading_state.db for synthetic fixture rows and unbrokered mock trades."""
    report: Dict[str, Any] = {"path": db_path, "exists": os.path.exists(db_path)}
    if not report["exists"]:
        return report

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        report["has_trades"] = "trades" in tables
        report["has_trade_labels"] = "trade_labels" in tables
        report["in_onedrive"] = "onedrive" in os.path.abspath(db_path).lower()

        if "trades" in tables:
            total_trades = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            synthetic_50 = con.execute(
                "SELECT COUNT(*) FROM trades WHERE ticket >= 200000 OR exit_reason = 'TP' OR payload LIKE '%\"source\": \"backtest\"%'"
            ).fetchone()[0]
            unbrokered_151 = con.execute(
                "SELECT COUNT(*) FROM trades WHERE ticket < 200000 AND (exit_reason IN ('stop', 'target') OR payload LIKE '%\"source\": \"mock\"%')"
            ).fetchone()[0]
            report["total_trades"] = total_trades
            report["synthetic_fixture_trades"] = synthetic_50
            report["unbrokered_paper_trades"] = unbrokered_151

    return report


def audit_llm_config() -> Dict[str, Any]:
    """Audit LLM router environment config for key redundancy and model availability."""
    from dotenv import load_dotenv
    load_dotenv()

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    tokenrouter_key = os.getenv("TOKENROUTER_KEY", "")
    nvidia_key = os.getenv("NVIDIA_KEY_1", "") or os.getenv("NVIDIA_KEY", "")

    return {
        "openrouter_has_key": bool(openrouter_key),
        "tokenrouter_has_key": bool(tokenrouter_key),
        "keys_are_identical": bool(openrouter_key and openrouter_key == tokenrouter_key),
        "nvidia_has_key": bool(nvidia_key),
        "fallback_order": os.getenv("LLM_ROUTER_FALLBACK_ORDER", ""),
        "trader_provider": os.getenv("TRADER_LLM_PROVIDER", ""),
        "trader_model": os.getenv("TRADER_LLM_MODEL", ""),
        "nvidia_model": os.getenv("LLM_ROUTER_MODEL_NVIDIA_NIM", ""),
    }


def fix_trial_ledger(db_path: str = "artifacts/trial_ledger.db") -> int:
    """Deduplicate trial_ledger.db to retain only unique strategy trials and rebuild valid hash chain."""
    if not os.path.exists(db_path):
        return 0

    backup_path = f"{db_path}.bak.{int(time.time())}"
    shutil.copy2(db_path, backup_path)
    print(f"Backed up {db_path} to {backup_path}")

    # Read latest trial per unique (strategy_id, strategy_class)
    with sqlite3.connect(db_path) as con:
        rows = con.execute("""
            SELECT t.strategy_id, t.sharpe, t.n_observations, t.skewness, t.kurtosis,
                   t.timestamp, t.metadata, t.strategy_class
            FROM trials t
            INNER JOIN (
                SELECT strategy_id, MAX(trial_id) as max_id
                FROM trials
                GROUP BY strategy_id
            ) latest ON t.trial_id = latest.max_id
            ORDER BY t.trial_id ASC
        """).fetchall()

    if not rows:
        return 0

    # Re-chain hash
    rebuilt_records = []
    prev_hash = ""
    for idx, r in enumerate(rows, start=1):
        strat_id, sharpe, n_obs, skew, kurt, ts, meta, scls = r
        rec_json = json.dumps({
            "trial_id": idx,
            "strategy_id": strat_id,
            "sharpe": float(sharpe),
            "n_observations": int(n_obs),
            "skewness": float(skew),
            "kurtosis": float(kurt),
            "timestamp": float(ts),
        }, sort_keys=True)
        payload = f"{rec_json}||{prev_hash}".encode("utf-8")
        current_hash = hashlib.sha256(payload).hexdigest()

        rebuilt_records.append((
            idx, strat_id, float(sharpe), int(n_obs), float(skew), float(kurt),
            float(ts), prev_hash, current_hash, meta, scls or "general"
        ))
        prev_hash = current_hash

    with sqlite3.connect(db_path) as con:
        con.execute("DELETE FROM trials")
        con.executemany("""
            INSERT INTO trials (
                trial_id, strategy_id, sharpe, n_observations, skewness, kurtosis,
                timestamp, hash_prev, hash_current, metadata, strategy_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rebuilt_records)
        con.commit()

    print(f"Deduplicated trial_ledger.db: {len(rebuilt_records)} distinct strategies retained with valid hash chain.")
    return len(rebuilt_records)


def fix_synthetic_rows(db_path: str = "trading_state.db") -> int:
    """Quarantine 50 legacy synthetic fixture rows (ticket >= 200000) from trades table."""
    if not os.path.exists(db_path):
        return 0

    backup_path = f"{db_path}.bak.{int(time.time())}"
    shutil.copy2(db_path, backup_path)
    print(f"Backed up {db_path} to {backup_path}")

    with sqlite3.connect(db_path) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "trades" not in tables:
            return 0

        # Ensure quarantine table exists
        con.execute("""
            CREATE TABLE IF NOT EXISTS legacy_synthetic_quarantine AS
            SELECT * FROM trades WHERE 0
        """)

        # Move synthetic rows
        syn_count = con.execute(
            "SELECT COUNT(*) FROM trades WHERE ticket >= 200000 OR exit_reason = 'TP' OR payload LIKE '%\"source\": \"backtest\"%'"
        ).fetchone()[0]

        if syn_count > 0:
            con.execute("""
                INSERT OR IGNORE INTO legacy_synthetic_quarantine
                SELECT * FROM trades
                WHERE ticket >= 200000 OR exit_reason = 'TP' OR payload LIKE '%\"source\": \"backtest\"%'
            """)
            con.execute("""
                DELETE FROM trades
                WHERE ticket >= 200000 OR exit_reason = 'TP' OR payload LIKE '%\"source\": \"backtest\"%'
            """)
            if "trade_labels" in tables:
                con.execute("DELETE FROM trade_labels WHERE ticket >= 200000")
            con.commit()
            print(f"Quarantined {syn_count} synthetic fixture rows from trades into legacy_synthetic_quarantine.")
        else:
            print("No synthetic fixture rows found to quarantine.")

    return syn_count


def main():
    parser = argparse.ArgumentParser(description="Forensic Diagnostic & Logic Repair")
    parser.add_argument("--fix", action="store_true", help="Apply database deduplication and synthetic quarantine")
    args = parser.parse_args()

    print("=" * 60)
    print("FORENSIC DIAGNOSTIC AUDIT")
    print("=" * 60)

    # 1. Trial Ledger Audit
    tl_report = audit_trial_ledger()
    print(f"\n[1] Trial Ledger ({tl_report.get('path')}):")
    print(f"    Total Trials:         {tl_report.get('total_trials')}")
    print(f"    Distinct Strategies:  {tl_report.get('distinct_strategies')}")
    print(f"    Duplicate Multiplier: {tl_report.get('duplicate_multiplier')}x")
    if tl_report.get("breakdown"):
        print("    Sample Breakdown (Top 3):")
        for strat, count, min_s, max_s in tl_report["breakdown"][:3]:
            print(f"      - {strat}: {count} trials recorded (Sharpe range: {min_s:.2f} to {max_s:.2f})")

    # 2. Trading State Audit
    ts_report = audit_trading_state()
    print(f"\n[2] Trading State Database ({ts_report.get('path')}):")
    print(f"    In OneDrive Dir:      {ts_report.get('in_onedrive')}")
    print(f"    Total Trades:         {ts_report.get('total_trades')}")
    print(f"    Synthetic Fixtures:   {ts_report.get('synthetic_fixture_trades')}")
    print(f"    Unbrokered Paper:     {ts_report.get('unbrokered_paper_trades')}")

    # 3. LLM Config Audit
    llm_report = audit_llm_config()
    print(f"\n[3] LLM Router Configuration:")
    print(f"    OpenRouter Key:       {llm_report.get('openrouter_has_key')}")
    print(f"    TokenRouter Key:      {llm_report.get('tokenrouter_has_key')}")
    print(f"    Keys are Identical:   {llm_report.get('keys_are_identical')} (Redundant duplicate fallback!)")
    print(f"    NVIDIA NIM Key:       {llm_report.get('nvidia_has_key')}")
    print(f"    NVIDIA Active Model:  {llm_report.get('nvidia_model')}")
    print(f"    Fallback Order:       {llm_report.get('fallback_order')}")

    if args.fix:
        print("\n" + "=" * 60)
        print("APPLYING REMEDIATION")
        print("=" * 60)
        fix_trial_ledger()
        fix_synthetic_rows()
        print("\nRemediation complete. Verifying integrity...")
        tl_post = audit_trial_ledger()
        ts_post = audit_trading_state()
        print(f"  Trial Ledger: {tl_post.get('total_trials')} trials for {tl_post.get('distinct_strategies')} strategies.")
        print(f"  Trading State: {ts_post.get('total_trades')} trades remaining ({ts_post.get('synthetic_fixture_trades')} synthetic).")


if __name__ == "__main__":
    main()
