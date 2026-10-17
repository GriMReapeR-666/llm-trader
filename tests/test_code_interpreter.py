"""Gate 0 contract tests for the dynamic metric code interpreter.

``DynamicMetricInterpreter.execute_code`` used to run agent-authored Python via
``exec``/``eval`` with **full builtins, no review and no timeout**
(AUDIT_2026-10-04_DEEP finding I1, rated red).  It is reachable by the decision
LLM through the ``execute_dynamic_metric_analysis`` tool, so a crafted headline
could run code on the host holding the broker credentials.  Gate 0 Phase 1
disarmed it (``docs/superpowers/plans/2026-10-04-gate-0-phase-1-hazards.md``):
``execute_code`` now refuses before parsing anything.

These tests therefore assert the REFUSAL contract, not arithmetic results.
Before the disarm, ``2 + 2 * 10`` returned ``22``; that capability is what the
finding asked to remove, so re-enabling it must be a deliberate, reviewed act
that also updates this file — not a side effect of an unrelated edit.

What is asserted here:

* the refusal is a well-formed ``CodeExecutionResult`` (the disarm stub once
  named a class that did not exist, and then omitted two required fields, so it
  raised ``TypeError`` instead of refusing);
* it refuses uniformly, *before* parsing, so malformed code is refused too;
* it is genuinely INERT — submitted code with an observable side effect does
  nothing, which is the property the audit actually cared about.
"""
from research.code_interpreter import DynamicMetricInterpreter, CodeExecutionResult

REFUSAL_SUBSTRING = "deactivated"


def _assert_refused(result, code):
    """Every refusal must be a well-formed, self-explaining result."""
    assert isinstance(result, CodeExecutionResult), (
        "execute_code must always return a CodeExecutionResult, got %r"
        % type(result).__name__)
    assert result.success is False, (
        "Gate 0 disarm regressed: execute_code ran %r (success=%r)"
        % (code[:60], result.success))
    assert result.result is None, "a refused execution must not report a result"
    assert result.error and REFUSAL_SUBSTRING in result.error, (
        "the refusal must say why: %r" % result.error)
    assert result.execution_time_ms >= 0


def test_interpreter_simple_expression():
    """A trivial arithmetic expression is refused, not evaluated to 22."""
    result = DynamicMetricInterpreter().execute_code("2 + 2 * 10")
    _assert_refused(result, "2 + 2 * 10")
    assert result.result != 22


def test_interpreter_pandas_numpy_metric():
    """The numpy/pandas metric script the decision LLM would submit is refused."""
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
    result = DynamicMetricInterpreter().execute_code(code)
    _assert_refused(result, code)
    assert not isinstance(result.result, dict)


def test_interpreter_function_calling_pattern():
    """Code defining a handler function is refused before it can be mounted."""
    code = """
def calculate_metric():
    prices = [100.0, 102.0, 101.5, 105.0, 104.0, 108.0]
    pnl = prices[-1] - prices[0]
    max_price = max(prices)
    return {"pnl": pnl, "max_price": max_price}
"""
    result = DynamicMetricInterpreter().execute_code(code)
    _assert_refused(result, code)


def test_interpreter_syntax_error_handling():
    """Refusal precedes parsing, so malformed code is refused identically.

    The disarm returns before ``compile``, so a syntax error is indistinguishable
    from valid code: neither is ever parsed.  That is the property to keep —
    asserting ``SyntaxError`` here would re-enable the parser.
    """
    result = DynamicMetricInterpreter().execute_code("def broken_syntax( {")
    _assert_refused(result, "def broken_syntax( {")
    assert "SyntaxError" not in result.error, (
        "code was parsed, so the Gate 0 disarm no longer short-circuits")


def test_interpreter_execution_is_inert(tmp_path):
    """The capability must be inert, not merely reported as disabled.

    A refusal string is cheap; this asserts the thing the audit was about.  The
    payload has two observable effects — writing a file and importing ``os`` —
    and neither may happen.  If someone deletes the disarm stub to "fix the
    failing tests", this fails loudly instead.
    """
    marker = tmp_path / "pwned.txt"
    code = (
        "import os\n"
        "open(r'%s', 'w').write('executed')\n"
        "os.getcwd()\n"
        "OUTPUT = {'cwd': os.getcwd()}\n"
    ) % marker

    result = DynamicMetricInterpreter().execute_code(code)

    assert not marker.exists(), (
        "execute_code ran the submitted code and wrote %s — the Gate 0 disarm "
        "has been removed" % marker)
    assert result.success is False
    assert result.result is None
    assert REFUSAL_SUBSTRING in (result.error or "")