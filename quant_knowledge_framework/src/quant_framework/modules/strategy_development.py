"""
Strategy Development Modules
=============================
Implements strategy validation, backtesting, and edge detection tools.
"""

import numpy as np
from scipy.stats import norm as _norm
from typing import Dict, List, Optional

from quant_framework.modules.interface import quant_module, quant_tool, QuantModule, registry


@quant_module(
    id="strategy_development.wfo",
    name="Walk-Forward Optimization Engine",
    version="1.0.0",
    category="strategy_development",
    tags=["wfo", "walk-forward", "optimization", "backtesting", "validation"],
)
class WalkForwardOptimizer(QuantModule):
    """
    Walk-Forward Optimization (WFO) engine for strategy parameter validation.
    """

    def __init__(self, is_window: int = 252, oos_window: int = 63, step_size: int = 21):
        self.is_window = is_window
        self.oos_window = oos_window
        self.step_size = step_size

    @quant_tool(
        module_id="strategy_development.wfo",
        name="strategy_development.wfo.optimize",
        description="Run walk-forward optimization on strategy returns",
        parameters={
            "returns": {"type": "array", "description": "Strategy return series"},
            "is_window": {"type": "int", "description": "In-sample window size", "default": 252},
            "oos_window": {"type": "int", "description": "Out-of-sample window size", "default": 63},
            "step_size": {"type": "int", "description": "Rolling step size", "default": 21},
        },
        returns={
            "wfe": {"type": "float", "description": "Walk-Forward Efficiency"},
            "is_sharpe": {"type": "float", "description": "In-sample Sharpe"},
            "oos_sharpe": {"type": "float", "description": "Out-of-sample Sharpe"},
            "robust": {"type": "bool", "description": "Whether strategy passes robustness check"},
        },
    )
    def optimize(self, returns: List[float], is_window: int = 252, oos_window: int = 63, step_size: int = 21) -> Dict:
        returns_arr = np.array(returns, dtype=float)
        n = len(returns_arr)

        if n < is_window + oos_window:
            return {"error": "Insufficient data for walk-forward analysis"}

        is_sharpe_list = []
        oos_sharpe_list = []

        for start in range(0, n - is_window - oos_window, step_size):
            is_end = start + is_window
            oos_end = is_end + oos_window

            is_returns = returns_arr[start:is_end]
            oos_returns = returns_arr[is_end:oos_end]

            is_sharpe_list.append(self._sharpe_ratio(is_returns))
            oos_sharpe_list.append(self._sharpe_ratio(oos_returns))

        avg_is = float(np.mean(is_sharpe_list)) if is_sharpe_list else 0.0
        avg_oos = float(np.mean(oos_sharpe_list)) if oos_sharpe_list else 0.0
        wfe = avg_oos / avg_is if avg_is > 0 else 0.0

        return {
            "wfe": float(wfe),
            "is_sharpe": avg_is,
            "oos_sharpe": avg_oos,
            "robust": bool(wfe >= 0.6),
            "windows": len(is_sharpe_list),
        }

    def _sharpe_ratio(self, returns: np.ndarray, risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        excess = returns - risk_free / 252
        mean = float(np.mean(excess))
        std = float(np.std(excess, ddof=1))
        return float(np.sqrt(252) * mean / std) if std > 0 else 0.0


@quant_module(
    id="strategy_development.dsr",
    name="Deflated Sharpe Ratio Calculator",
    version="2.0.0",
    category="strategy_development",
    tags=["dsr", "deflated sharpe", "overfitting", "multiple testing"],
)
class DeflatedSharpeRatio(QuantModule):
    """
    Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014, "The Deflated
    Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and
    Non-Normality", Journal of Portfolio Management 40(5)).

    DSR = PSR(SR*) : probability that the true SR exceeds the expected
    maximum SR of `trials` independent strategy variants,

        SR0 = sqrt(V[SR_n]) * ( (1-gamma)*Phi^-1(1 - 1/T) + gamma*Phi^-1(1 - 1/(T*e)) )
        DSR = Phi( (SR - SR0) * sqrt(n-1) / sqrt(1 - gamma3*SR + (gamma4-1)/4 * SR^2) )

    with skew gamma3 and kurtosis gamma4 of the per-period returns (PSR's
    non-normality correction), gamma = Euler-Mascheroni, and
    V[SR_n] the cross-trial variance of estimated Sharpe ratios when
    supplied (fallback: the homoskedastic 1/(n-1) approximation).

    Unlike the previous v1 implementation (which used a Bonferroni-style
    p-value and ignored skew/kurtosis, making results wildly optimistic),
    this returns an actual probability in [0, 1]: DSR > 0.95 is the usual
    significance bar.
    """

    _GAMMA = 0.5772156649015329  # Euler-Mascheroni constant

    def __init__(self, trials: int = 1000):
        self.trials = trials

    @quant_tool(
        module_id="strategy_development.dsr",
        name="strategy_development.dsr.calculate",
        description="Deflated Sharpe Ratio (Bailey/Lopez de Prado 2014) with skew and kurtosis correction",
        parameters={
            "sharpe_ratio": {"type": "float", "description": "Observed (non-annualized) Sharpe ratio"},
            "n": {"type": "int", "description": "Number of observations/returns"},
            "trials": {"type": "int", "description": "Number of independent strategy variants tested", "default": 1000},
            "skew": {"type": "float", "description": "Skewness of returns (required for full correction)", "default": 0},
            "kurtosis": {"type": "float", "description": "Kurtosis of returns (raw, not excess; default 3 = Gaussian)", "default": 3},
            "sr_variance": {"type": "float", "description": "Cross-trial variance of estimated SRs; defaults to 1/(n-1)", "required": False},
        },
        returns={
            "dsr": {"type": "float", "description": "Deflated Sharpe Ratio (probability, 0-1)"},
            "p_value": {"type": "float", "description": "1 - DSR"},
            "significant": {"type": "bool", "description": "DSR > 0.95"},
            "expected_max_sr": {"type": "float", "description": "SR0: expected max SR under multiple testing"},
        },
    )
    def calculate(
        self,
        sharpe_ratio: float,
        n: int,
        trials: int = 1000,
        skew: float = 0.0,
        kurtosis: float = 3.0,
        sr_variance: Optional[float] = None,
    ) -> Dict:
        if n < 3:
            return {"error": "Insufficient observations"}
        if trials < 1:
            return {"error": "trials must be >= 1"}
        if kurtosis < 1:
            return {"error": "kurtosis must be >= 1"}

        # SR0: expected maximum Sharpe under `trials` independent variants.
        var_sr = float(sr_variance) if sr_variance and sr_variance > 0 else 1.0 / (n - 1)
        gamma = self._GAMMA
        t = float(trials)
        if trials == 1:
            # Single trial: no selection bias, so the null benchmark SR is 0.
            sr0 = 0.0
        else:
            sr0 = float(np.sqrt(var_sr) * ((1 - gamma) * _norm.ppf(1 - 1.0 / t) + gamma * _norm.ppf(1 - 1.0 / (t * np.e))))

        # PSR-style denominator with skew/kurtosis non-normality correction.
        denom_sq = 1.0 - skew * sharpe_ratio + ((kurtosis - 1.0) / 4.0) * sharpe_ratio ** 2
        if denom_sq <= 0:
            return {"error": "Invalid skew/kurtosis/SR combination (non-positive PSR variance)"}

        dsr = float(_norm.cdf((sharpe_ratio - sr0) * np.sqrt(n - 1) / np.sqrt(denom_sq)))
        dsr = float(min(1.0, max(0.0, dsr)))
        return {
            "dsr": dsr,
            "p_value": float(1.0 - dsr),
            "significant": bool(dsr > 0.95),
            "expected_max_sr": float(sr0),
            "sr_variance_used": float(var_sr),
        }


@quant_module(
    id="strategy_development.triple_barrier",
    name="Triple Barrier Labeling",
    version="1.0.0",
    category="strategy_development",
    tags=["triple barrier", "labeling", "meta-labeling", "author-c"],
)
class TripleBarrierLabeler(QuantModule):
    """
    Implements the Triple Barrier Method for labeling trading opportunities.
    """

    def __init__(self, take_profit: float = 0.05, stop_loss: float = -0.05, max_hold: int = 20):
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self.max_hold = max_hold

    @quant_tool(
        module_id="strategy_development.triple_barrier",
        name="strategy_development.triple_barrier.label",
        description="Label price path using triple barrier method",
        parameters={
            "prices": {"type": "array", "description": "Price series"},
            "entry_price": {"type": "float", "description": "Entry price"},
            "take_profit": {"type": "float", "description": "Take-profit barrier", "default": 0.05},
            "stop_loss": {"type": "float", "description": "Stop-loss barrier", "default": -0.05},
            "max_hold": {"type": "int", "description": "Maximum holding periods", "default": 20},
        },
        returns={
            "label": {"type": "int", "description": "Label: 1 (TP), -1 (SL), 0 (time)"},
            "return": {"type": "float", "description": "Actual return at exit"},
            "exit_bar": {"type": "int", "description": "Bar when barrier was hit"},
        },
    )
    def label(self, prices: List[float], entry_price: float, take_profit: float = 0.05, stop_loss: float = -0.05, max_hold: int = 20) -> Dict:
        prices_arr = np.array(prices, dtype=float)
        n = len(prices_arr)
        if n == 0:
            return {"label": 0, "return": 0.0, "exit_bar": 0}

        returns = (prices_arr - entry_price) / entry_price
        tp_bar = int(np.argmax(returns >= take_profit)) if np.any(returns >= take_profit) else n
        sl_bar = int(np.argmax(returns <= stop_loss)) if np.any(returns <= stop_loss) else n

        if tp_bar < sl_bar and tp_bar < max_hold:
            label = 1
            exit_bar = tp_bar
            actual_return = take_profit
        elif sl_bar < tp_bar and sl_bar < max_hold:
            label = -1
            exit_bar = sl_bar
            actual_return = stop_loss
        else:
            label = 0
            exit_bar = min(max_hold - 1, n - 1)
            actual_return = float(returns[exit_bar]) if exit_bar < n else 0.0

        return {"label": int(label), "return": float(actual_return), "exit_bar": int(exit_bar)}
