"""Unit tests for openbb_provider_registry.py."""
import pytest
from openbb_provider_registry import (
    PROVIDERS,
    DATA_TYPES,
    active_providers,
    unique_endpoint_count,
    providers_by_type,
    dedup_report,
)


def test_registry_contains_18_providers():
    """Verify all 18 registered providers exist in registry."""
    expected_providers = {
        "cboe", "ecb", "econdb", "famafrench", "federal_reserve",
        "finra", "finviz", "government_us", "imf", "multpl",
        "nasdaq", "portfolio123", "sec", "seeking_alpha", "stockgrid", "tmx",
        "wsj", "yfinance",
    }
    assert set(PROVIDERS.keys()) == expected_providers
    assert len(PROVIDERS) == 18


def test_provider_data_types():
    """Verify every provider is assigned a valid data_type from DATA_TYPES."""
    for name, cfg in PROVIDERS.items():
        assert cfg.data_type in DATA_TYPES, f"Provider '{name}' has unknown data_type '{cfg.data_type}'"


def test_active_providers():
    """Verify active_providers returns providers that have non-skipped fetchers."""
    active = active_providers()
    assert len(active) > 0
    for name, cfg in active.items():
        assert len(cfg.active_fetchers) > 0


def test_dedup_filtering():
    """Verify duplicate endpoints (like YFinance equity OHLCV or FRED fed funds rate) are skipped with reasons."""
    report = dedup_report()
    assert "yfinance" in report
    assert "federal_reserve" in report
    assert "sec" in report

    # Check specific skipped fetchers
    yf_cfg = PROVIDERS["yfinance"]
    yf_skipped = [f.name for f in yf_cfg.fetchers if f.skip]
    assert "YFinanceEquityHistorical" in yf_skipped
    assert "YFinanceEquityQuote" in yf_skipped

    sec_cfg = PROVIDERS["sec"]
    sec_skipped = [f.name for f in sec_cfg.fetchers if f.skip]
    assert "SecCompanyFilings" in sec_skipped


def test_unique_endpoint_count():
    """Verify unique endpoint count is accurate and positive."""
    count = unique_endpoint_count()
    assert count > 40


def test_providers_by_type():
    """Verify providers_by_type returns matching provider configs."""
    central_banks = providers_by_type("central-bank-release")
    names = [p.name for p in central_banks]
    assert "ecb" in names
    assert "federal_reserve" in names

    microstructure = providers_by_type("market-microstructure")
    micro_names = [p.name for p in microstructure]
    assert "finra" in micro_names
    assert "stockgrid" in micro_names
