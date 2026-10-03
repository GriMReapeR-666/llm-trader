"""Public Export Module for Retail Quantitative Models.

Aggregates all 11 document-derived models into a single import point:
1. SPRT Strategy Kill (Part2 §6.9)
2. CUSUM Residual Monitor (Part2 §5.3)
3. Alpha Capacity Decay (Part1 §3.8)
4. Retail EV with Friction (Part1 §1.2)
5. Bayesian Win Rate Update (Part2 §6.8)
6. Correlation Breakdown Detection (Part2 §6.7)
7. Sentiment Veto (Part1 §3.5-3.6)
8. Min Viable Account Size (Part1 §1.2)
9. Session Spread Multiplier (Part1 §1.6)
10. OU Time Stop (Part3 §10.1)
11. Portfolio De-Correlation 4-Sleeve (Part3 §9.4)
"""
from __future__ import annotations

from core.sprt_strategy_test import (
    SPRTStrategyTest,
    SPRTResult,
    WaldSPRT,
    SequentialProbabilityRatioTest,
)
from core.cusum_monitor import (
    CUSUMMonitor,
    CUSUMState,
    CUSUMAlert,
    CUSUMStructuralBreakDetector,
    CUSUMResidualMonitor,
    CumulativeSumMonitor,
    CUSUMControlChart,
)
from core.alpha_capacity import (
    AlphaCapacityModel,
    CapacityResult,
    AlphaCapacityDecayModel,
    StrategyCapacityEvaluator,
)
from core.retail_friction import (
    RetailFrictionEngine,
    RetailFrictionResult,
    RetailExecutionFriction,
    MinimumViableAccountCalculator,
)
from core.bayesian_update import (
    BayesianWinRateUpdater,
    BayesianState,
    BayesianBetaUpdater,
    OnlineWinRateBayesian,
)
from core.correlation_breakdown import (
    CorrelationBreakdownDetector,
    CorrelationBreakdownResult,
    CorrelationInstabilityDetector,
    PortfolioCorrelationMonitor,
)
from core.sentiment_veto import (
    SentimentVetoEngine,
    SentimentVetoResult,
    SentimentFilter,
    ContrarianSentimentEngine,
)
from core.account_sizing import (
    AccountSizingEngine,
    AccountSizingResult,
    MinimumViableAccountCalculator as _AccountSizingAlias,
    PositionSizer1R,
    calculate_universal_1r,
)
from core.session_spread import (
    SessionSpreadEngine,
    SessionSpreadResult,
    FXSessionSpreadModel,
    TradingSessionCostModel,
)
from core.ou_time_stop import (
    OUTimeStopEngine,
    OUTimeStopResult,
    OUParameters,
    OrnsteinUhlenbeckTimeStop,
    MeanReversionTimeStop,
)
from core.portfolio_decorr import (
    PortfolioDeCorrelationEngine,
    DeCorrelationResult,
    PortfolioVarianceReduction,
    FourSleeveDeCorrelation,
)

__all__ = [
    # 1. SPRT Strategy Kill
    "SPRTStrategyTest",
    "SPRTResult",
    "WaldSPRT",
    "SequentialProbabilityRatioTest",
    # 2. CUSUM Monitor
    "CUSUMMonitor",
    "CUSUMState",
    "CUSUMAlert",
    "CUSUMStructuralBreakDetector",
    "CUSUMResidualMonitor",
    "CumulativeSumMonitor",
    "CUSUMControlChart",
    # 3. Alpha Capacity
    "AlphaCapacityModel",
    "CapacityResult",
    "AlphaCapacityDecayModel",
    "StrategyCapacityEvaluator",
    # 4. Retail Friction
    "RetailFrictionEngine",
    "RetailFrictionResult",
    "RetailExecutionFriction",
    # 5. Bayesian Update
    "BayesianWinRateUpdater",
    "BayesianState",
    "BayesianBetaUpdater",
    "OnlineWinRateBayesian",
    # 6. Correlation Breakdown
    "CorrelationBreakdownDetector",
    "CorrelationBreakdownResult",
    "CorrelationInstabilityDetector",
    "PortfolioCorrelationMonitor",
    # 7. Sentiment Veto
    "SentimentVetoEngine",
    "SentimentVetoResult",
    "SentimentFilter",
    "ContrarianSentimentEngine",
    # 8. Account Sizing
    "AccountSizingEngine",
    "AccountSizingResult",
    "PositionSizer1R",
    "calculate_universal_1r",
    # 9. Session Spread
    "SessionSpreadEngine",
    "SessionSpreadResult",
    "FXSessionSpreadModel",
    "TradingSessionCostModel",
    # 10. OU Time Stop
    "OUTimeStopEngine",
    "OUTimeStopResult",
    "OUParameters",
    "OrnsteinUhlenbeckTimeStop",
    "MeanReversionTimeStop",
    # 11. Portfolio De-Correlation
    "PortfolioDeCorrelationEngine",
    "DeCorrelationResult",
    "PortfolioVarianceReduction",
    "FourSleeveDeCorrelation",
]
