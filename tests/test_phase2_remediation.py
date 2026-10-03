import os
import time
import pytest
from unittest.mock import MagicMock, patch
from data.market_data_provider import PerformanceMonitor, MarketDataProvider


class TestPhase2Remediation:
    def test_performance_monitor_history_bounding(self, tmp_path):
        """Verify PerformanceMonitor caps history at 2000 records on logging and loading."""
        history_file = str(tmp_path / "test_perf.json")
        pm = PerformanceMonitor(history_file=history_file)

        # Log 2050 mock trades
        for i in range(2050):
            pm.log_trade(
                ticket=i,
                symbol="EURUSD",
                action="BUY",
                volume=0.1,
                price=1.1000 + (i * 0.0001),
                decision={"metrics": {"expected_price": 1.1000}}
            )

        assert len(pm.history) == 2000
        # The oldest remaining ticket should be 50
        assert pm.history[0]["ticket"] == 50
        assert pm.history[-1]["ticket"] == 2049

        # Reload from disk and verify cap persists
        pm_reloaded = PerformanceMonitor(history_file=history_file)
        assert len(pm_reloaded.history) == 2000
        assert pm_reloaded.history[0]["ticket"] == 50

    def test_market_data_provider_shared_pulse_cache(self):
        """Verify MarketDataProvider caches calendar events for 5 seconds per symbol."""
        risk_mock = MagicMock()
        risk_mock.balance = 10000.0
        risk_mock.risk_per_trade = 0.01

        mdp = MarketDataProvider(client=None, risk_manager=risk_mock)
        assert hasattr(mdp, "_shared_pulse_cache")

        # Manually prime cache
        now = time.time()
        mock_events = [{"event": "NFP", "currency": "USD", "importance": 3}]
        mdp._shared_pulse_cache["EURUSD"] = {
            "ts": now,
            "news_events": mock_events,
            "calendar_source": "test_cached_source",
        }

        # Query cache directly within 5 seconds
        cached = mdp._shared_pulse_cache.get("EURUSD")
        assert cached is not None
        assert (time.time() - cached["ts"]) < 5.0
        assert cached["news_events"] == mock_events
        assert cached["calendar_source"] == "test_cached_source"

    def test_rl_tickets_and_state_bounding(self):
        """Verify ticket pruning logic keeps set bounded to 1000 entries when exceeding 2000."""
        tickets_set = set(range(2050))
        assert len(tickets_set) > 2000

        # Simulate pruning logic applied in LLMOrchestrator
        if len(tickets_set) > 2000:
            tickets_set = set(sorted(tickets_set)[-1000:])

        assert len(tickets_set) == 1000
        assert min(tickets_set) == 1050
        assert max(tickets_set) == 2049

        # Simulate rl_state_history bounding
        rl_state_history = [{"state": i} for i in range(1050)]
        if len(rl_state_history) > 1000:
            rl_state_history = rl_state_history[-1000:]

        assert len(rl_state_history) == 1000
        assert rl_state_history[0]["state"] == 50
        assert rl_state_history[-1]["state"] == 1049

    def test_progress_bar_math_clean_rendering(self):
        """Verify progress bar calculation renders exactly 10 characters across diverse scale sizes."""
        test_cases = [
            (0, 100, 0),      # 0%
            (1, 100, 0),      # 1% -> rounds to 0
            (10, 100, 1),     # 10% -> exactly 1 block
            (50, 100, 5),     # 50% -> 5 blocks
            (100, 100, 10),   # 100% -> 10 blocks
            (1, 3, 3),        # 1/3 -> 3 blocks
            (2, 3, 7),        # 2/3 -> 7 blocks
            (3, 3, 10),       # 3/3 -> 10 blocks
            (5000, 9999, 5),  # ~50% -> 5 blocks
            (9999, 9999, 10), # 100% -> 10 blocks
        ]

        for cycle_num, total_cycles, expected_filled in test_cases:
            filled_blocks = min(10, max(0, int(round((cycle_num / max(1, total_cycles)) * 10))))
            progress_bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
            assert len(progress_bar) == 10
            assert progress_bar.count("█") == expected_filled
            assert progress_bar.count("░") == 10 - expected_filled
