"""TradingView external-alert routing branch (plan step 5).

External TV alerts route as a standard LIMIT at HIGH urgency (never IOC
taker), and are advisory-only (volume 0, no order) at MEDIUM/LOW.
Non-TV sources keep the deterministic router unchanged.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.urgency_sor import UrgencySmartOrderRouter  # noqa: E402


@pytest.fixture
def router():
    return UrgencySmartOrderRouter(high_urgency_threshold=0.70,
                                   low_urgency_threshold=0.30)


class TestTradingViewBranch:
    def test_high_urgency_tv_alert_routes_limit_not_ioc(self, router):
        out = router.route_external_alert(
            symbol="EURUSD", side="BUY", volume=0.1, price=1.0855,
            urgency_score=0.85, best_bid=1.0850, best_ask=1.0860,
            source="tradingview")
        assert out["order_type"] == "LIMIT_STANDARD"
        assert out["is_taker"] is False
        assert out["target_price"] == pytest.approx(1.0855)
        assert out["execution_strategy"] == "EXTERNAL_ALERT_STANDARD_LIMIT"

    def test_medium_urgency_tv_alert_is_advisory_only(self, router):
        out = router.route_external_alert(
            symbol="EURUSD", side="SELL", volume=0.2, price=1.0870,
            urgency_score=0.50, best_bid=1.0850, best_ask=1.0860,
            source="tradingview")
        assert out["order_type"] is None
        assert out["volume"] == 0.0
        assert out["execution_strategy"] == "EXTERNAL_ALERT_ADVISORY_ONLY"

    def test_low_urgency_tv_alert_is_advisory_only(self, router):
        out = router.route_external_alert(
            symbol="EURUSD", side="BUY", volume=0.1, price=1.0855,
            urgency_score=0.10, best_bid=1.0850, best_ask=1.0860,
            source="tradingview")
        assert out["order_type"] is None
        assert out["volume"] == 0.0


class TestNonTVUnchanged:
    def test_other_source_keeps_deterministic_router(self, router):
        out = router.route_external_alert(
            symbol="EURUSD", side="BUY", volume=0.1, price=1.0855,
            urgency_score=0.85, best_bid=1.0850, best_ask=1.0860,
            source="internal_signal")
        # Deterministic router is unchanged: high urgency -> IOC taker.
        assert out["order_type"] == "MARKET_IOC"
        assert out["is_taker"] is True

    def test_default_route_execution_order_untouched(self, router):
        out = router.route_execution_order(
            symbol="EURUSD", side="BUY", volume=0.1, price=1.0855,
            urgency_score=0.85, best_bid=1.0850, best_ask=1.0860)
        assert out["order_type"] == "MARKET_IOC"
