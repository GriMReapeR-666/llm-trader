"""Unit tests for the sandboxed dynamic metric code interpreter."""
import pytest
from research.code_interpreter import DynamicMetricInterpreter, CodeExecutionResult


def test_interpreter_simple_expression():
    interpreter = DynamicMetricInterpreter()
    result = interpreter.execute_code("2 + 2 * 10")
    assert isinstance(result, CodeExecutionResult)
    assert result.success is True
    assert result.result == 22


def test_interpreter_pandas_numpy_metric():
    interpreter = DynamicMetricInterpreter()
    code = """
import numpy as np
import pandas as pd

returns = np.array([0.01, -0.005, 0.015, 0.02, -0.01, 0.008])
sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252))
win_rate = float(np.sum(returns > 0) / len(returns) * 100.0)

OUTPUT = {
    "sharpe": round(sharpe, 3),
    "win_rate": round(win_rate, 2),
    "sample_size": len(returns)
}
"""
    result = interpreter.execute_code(code)
    assert result.success is True
    assert isinstance(result.result, dict)
    assert "sharpe" in result.result
    assert result.result["win_rate"] == 66.67
    assert result.result["sample_size"] == 6


def test_interpreter_function_calling_pattern():
    interpreter = DynamicMetricInterpreter()
    code = """
def calculate_metric():
    prices = [100.0, 102.0, 101.5, 105.0, 104.0, 108.0]
    pnl = prices[-1] - prices[0]
    max_price = max(prices)
    return {"pnl": pnl, "max_price": max_price}
"""
    result = interpreter.execute_code(code)
    assert result.success is True
    assert result.result["pnl"] == 8.0
    assert result.result["max_price"] == 108.0


def test_interpreter_syntax_error_handling():
    interpreter = DynamicMetricInterpreter()
    result = interpreter.execute_code("def broken_syntax( {")
    assert result.success is False
    assert result.error is not None
    assert "SyntaxError" in result.error
