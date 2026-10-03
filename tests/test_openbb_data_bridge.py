"""Unit tests for openbb_data_bridge.py."""
import pytest
from unittest.mock import MagicMock, patch
from openbb_data_bridge import OpenBBDataBridge, ProviderHealth, RateLimitGate


def test_bridge_initialization_without_store():
    """Verify bridge initializes gracefully without a store."""
    bridge = OpenBBDataBridge()
    assert bridge.provider_count() == 18
    assert bridge.endpoint_count() > 40


def test_bridge_provider_status():
    """Verify provider_status returns health dictionary for all providers."""
    bridge = OpenBBDataBridge()
    status = bridge.provider_status()
    assert "cboe" in status
    assert "ecb" in status
    assert status["ecb"]["circuit"] == "CLOSED"
    assert status["ecb"]["data_type"] == "central-bank-release"


def test_provider_health_circuit_breaker():
    """Verify ProviderHealth trips after max_failures and recovers."""
    health = ProviderHealth(max_failures=3, cooldown_seconds=60)
    assert health.is_open("test_provider") is True

    health.record_failure("test_provider")
    health.record_failure("test_provider")
    assert health.is_open("test_provider") is True

    health.record_failure("test_provider")
    assert health.is_open("test_provider") is False
    assert health.status("test_provider")["circuit"] == "OPEN"

    health.record_success("test_provider")
    assert health.is_open("test_provider") is True


def test_rate_limit_gate():
    """Verify RateLimitGate does not error when called."""
    gate = RateLimitGate()
    gate.wait_if_needed("test_provider", rpm=600)  # fast rpm for test


def test_fetch_and_ingest_skips_deduped_fetcher():
    """Verify fetch_and_ingest blocks skipped (deduped) fetchers."""
    bridge = OpenBBDataBridge()
    ok, msg = bridge.fetch_and_ingest("yfinance", "YFinanceEquityHistorical")
    assert ok is False
    assert "dedup" in msg


def test_fetch_and_ingest_handles_missing_openbb():
    """Verify fetch_and_ingest handles missing OpenBB gracefully."""
    with patch("openbb_data_bridge.OPENBB_AVAILABLE", False):
        bridge = OpenBBDataBridge()
        ok, msg = bridge.fetch_and_ingest("cboe", "CboeAvailableIndices")
        assert ok is False
        assert "not installed" in msg


def test_fetch_by_type():
    """Verify fetch_by_type filters by data_type."""
    bridge = OpenBBDataBridge()
    with patch.object(bridge, "fetch_and_ingest", return_value=(False, "mocked")):
        results = bridge.fetch_by_type("central-bank-release")
        assert len(results) > 0
        for name, ok, msg in results:
            assert ok is False
            assert msg == "mocked"


def test_api_manager_integration():
    """Verify APIManager initializes OpenBBDataBridge."""
    from api_manager import APIManager
    api = APIManager()
    assert hasattr(api, "openbb")
    assert api.openbb is not None
    status = api.get_status()
    assert "OpenBB" in status


def test_unsupported_provider_fast_fails():
    """Verify endpoints with unsupported providers fast-fail without retrying."""
    bridge = OpenBBDataBridge()
    ok, msg = bridge.fetch_and_ingest("cboe", "CboeAvailableIndices")
    assert ok is False
    assert "not supported" in msg or "not installed" in msg


def test_missing_required_params_fast_fails():
    """Verify endpoints with missing mandatory params fast-fail before calling SDK."""
    bridge = OpenBBDataBridge()
    ok, msg = bridge.fetch_and_ingest("econdb", "EconDbCountryProfile", params={})
    assert ok is False
    assert "requires parameters" in msg or "not installed" in msg
