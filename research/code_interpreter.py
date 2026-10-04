"""Sandboxed Dynamic Python Code Interpreter for Quantitative & Metric Calculations.

Empowers trading agents (QuantitativeCoach, MarketModel, MacroAnalyst) to write,
validate, and execute custom Python functions on the fly for calculating arbitrary
metrics, analyzing trading journal data, and running quantitative algorithms.
"""
from __future__ import annotations

import io
import sys
import math
import json
import sqlite3
import traceback
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd

from research.vbt_engine import VectorizedBacktestEngine, BacktestMetrics


@dataclass
class CodeExecutionResult:
    """Structured result returned from dynamic code execution."""
    success: bool
    result: Any
    stdout: str
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "result": self.result,
            "stdout": self.stdout,
            "error": self.error,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "created_at": self.created_at,
        }


class DynamicMetricInterpreter:
    """Sandboxed execution engine with pre-injected trading utilities and read-only data access."""

    def __init__(self, db_path: str = "trading_state.db"):
        self.db_path = db_path

    def _get_ro_connection(self) -> sqlite3.Connection:
        """Create a read-only SQLite connection to prevent unauthorized database modifications."""
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro" if "file:" not in self.db_path else self.db_path, uri=True)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------
    # Pre-injected Read-Only Data Helpers for Agents
    # ------------------------------------------------------------------

    def get_trades_df(self, symbol: Optional[str] = None, limit: int = 500) -> pd.DataFrame:
        """Fetch broker-reconciled historical trades as a clean Pandas DataFrame.

        Without independent close evidence the frame is empty: a self-declared
        journal row must never reach an agent as a performance fact.
        """
        try:
            try:
                from research_validation import broker_reconciled_tickets
            except ImportError:
                from research.research_validation import broker_reconciled_tickets
            tickets = sorted(broker_reconciled_tickets(self.db_path))
            if not tickets:
                raise RuntimeError("no broker-reconciled trades")
            with sqlite3.connect(self.db_path) as con:
                query = "SELECT * FROM trades WHERE 1=1"
                params: List[Any] = []
                if symbol:
                    query += " AND symbol = ?"
                    params.append(str(symbol).upper())
                query += " AND ticket IN (%s)" % ",".join("?" for _ in tickets)
                params.extend(tickets)
                query += " ORDER BY closed_at DESC LIMIT ?"
                params.append(int(limit))
                df = pd.read_sql_query(query, con, params=params)
                return df
        except Exception:
            return pd.DataFrame(columns=["ticket", "symbol", "side", "volume", "entry", "stop_loss", "take_profit", "pnl", "opened_at", "closed_at"])

    def get_proposals_df(self, symbol: Optional[str] = None, limit: int = 500) -> pd.DataFrame:
        """Fetch historical trade proposals with approval and rejection reasons."""
        try:
            with sqlite3.connect(self.db_path) as con:
                query = "SELECT * FROM proposals WHERE 1=1"
                params: List[Any] = []
                if symbol:
                    query += " AND symbol = ?"
                    params.append(str(symbol).upper())
                query += " ORDER BY created_at DESC LIMIT ?"
                params.append(int(limit))
                df = pd.read_sql_query(query, con, params=params)
                return df
        except Exception:
            return pd.DataFrame(columns=["id", "created_at", "strategy", "symbol", "side", "approved", "reasons"])

    def get_atomic_facts_df(self, symbol: Optional[str] = None, limit: int = 200) -> pd.DataFrame:
        """Fetch L1 atomic facts from trading memory."""
        try:
            with sqlite3.connect(self.db_path) as con:
                query = "SELECT * FROM atomic_facts WHERE 1=1"
                params: List[Any] = []
                if symbol:
                    query += " AND (fact_key LIKE ? OR fact_value LIKE ?)"
                    params.extend([f"%{symbol}%", f"%{symbol}%"])
                query += " ORDER BY observed_at DESC LIMIT ?"
                params.append(int(limit))
                return pd.read_sql_query(query, con, params=params)
        except Exception:
            return pd.DataFrame(columns=["id", "observed_at", "fact_key", "fact_value", "fact_status", "confidence"])

    def get_scenarios_df(self, limit: int = 50) -> pd.DataFrame:
        """Fetch L2 scenario documents and win/loss statistics."""
        try:
            with sqlite3.connect(self.db_path) as con:
                query = "SELECT * FROM scenario_documents ORDER BY updated_at DESC LIMIT ?"
                return pd.read_sql_query(query, con, params=[int(limit)])
        except Exception:
            return pd.DataFrame(columns=["scenario_key", "sample_count", "win_count", "loss_count", "status"])

    # ------------------------------------------------------------------
    # Dynamic Code Execution
    # ------------------------------------------------------------------

    def execute_code(
        self,
        code_str: str,
        custom_context: Optional[Dict[str, Any]] = None,
    ) -> CodeExecutionResult:
        """Execute arbitrary agent-written Python code safely and capture metrics/stdout.
        
        Args:
            code_str: Python code defining logic or returning a metric dictionary.
            custom_context: Additional parameters or variables to inject.
        """
        # Gate 0 Security: Arbitrary runtime code execution deactivated
        return ExecutionResult(
            success=False,
            error="Arbitrary code execution via DynamicMetricInterpreter has been deactivated for security.",
            execution_time_ms=0.0,
        )

        start_time = datetime.now(timezone.utc)
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # Build safe execution scope
        vbt_engine = VectorizedBacktestEngine()
        scope: Dict[str, Any] = {
            # Standard Math & Data Libraries
            "np": np,
            "pd": pd,
            "math": math,
            "json": json,
            "datetime": datetime,
            "timezone": timezone,
            "timedelta": timedelta,
            # Quantitative Backtest Engine
            "VectorizedBacktestEngine": VectorizedBacktestEngine,
            "vbt_engine": vbt_engine,
            # Read-Only Database Data Fetchers
            "get_trades_df": self.get_trades_df,
            "get_proposals_df": self.get_proposals_df,
            "get_atomic_facts_df": self.get_atomic_facts_df,
            "get_scenarios_df": self.get_scenarios_df,
            # Pre-injected Result Container
            "OUTPUT": None,
        }

        if custom_context:
            scope.update(custom_context)

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                # If code is a single expression, evaluate it
                parsed = None
                try:
                    compiled = compile(code_str, "<agent_script>", "eval")
                    parsed = eval(compiled, scope)
                except SyntaxError:
                    # Otherwise execute as a multi-line script
                    compiled = compile(code_str, "<agent_script>", "exec")
                    exec(compiled, scope)
                    # Check if script set 'OUTPUT' or a function named 'calculate_metric' / 'main'
                    if scope.get("OUTPUT") is not None:
                        parsed = scope.get("OUTPUT")
                    elif callable(scope.get("calculate_metric")):
                        parsed = scope["calculate_metric"]()
                    elif callable(scope.get("main")):
                        parsed = scope["main"]()
                    elif callable(scope.get("run")):
                        parsed = scope["run"]()
                    else:
                        # Find any returned metric dictionary in scope
                        user_vars = {k: v for k, v in scope.items() if not k.startswith("_") and k not in (
                            "np", "pd", "math", "json", "datetime", "timezone", "timedelta",
                            "VectorizedBacktestEngine", "vbt_engine", "get_trades_df",
                            "get_proposals_df", "get_atomic_facts_df", "get_scenarios_df", "OUTPUT"
                        )}
                        parsed = user_vars

            elapsed_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000.0
            
            # Serialize result to standard Python types
            if isinstance(parsed, (pd.DataFrame, pd.Series)):
                clean_result = parsed.to_dict()
            elif isinstance(parsed, np.ndarray):
                clean_result = parsed.tolist()
            elif isinstance(parsed, (np.floating, np.integer)):
                clean_result = parsed.item()
            elif isinstance(parsed, BacktestMetrics):
                clean_result = parsed.to_dict()
            else:
                clean_result = parsed

            return CodeExecutionResult(
                success=True,
                result=clean_result,
                stdout=stdout_buf.getvalue().strip(),
                error=None,
                execution_time_ms=elapsed_ms,
            )

        except Exception as exc:
            elapsed_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000.0
            tb = traceback.format_exc()
            return CodeExecutionResult(
                success=False,
                result=None,
                stdout=stdout_buf.getvalue().strip(),
                error=f"{type(exc).__name__}: {str(exc)}\n{tb}",
                execution_time_ms=elapsed_ms,
            )
