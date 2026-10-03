from data_requirements import evaluate


def test_dom_required_setup_fails_closed_on_non_numeric_depth():
    result = evaluate(
        "liquidity_sweep_reversal_v1",
        {"dom": {"available": True, "dom_depth": "deep", "captured_at": "2026-08-06T00:00:00Z"}},
        calendar_available=True,
    )
    assert result["available"] is False
    assert "DOM unavailable for DOM-required setup" in result["reasons"]


def test_dom_required_setup_accepts_strict_valid_payload():
    result = evaluate(
        "liquidity_sweep_reversal_v1",
        {"dom": {"available": True, "dom_depth": 5, "captured_at": "2026-08-06T00:00:00Z"}},
        calendar_available=True,
    )
    assert result["available"] is True


def test_malformed_snapshot_and_calendar_flag_fail_closed():
    result = evaluate("trend_pullback_v1", None, calendar_available="yes")
    assert result["available"] is False
    assert "market-data snapshot is missing or malformed" in result["reasons"]
    assert "calendar availability flag is malformed" in result["reasons"]


def test_cross_market_requires_mapping_payload():
    result = evaluate("macro_continuation_v1", {"macro_data": []}, calendar_available=True)
    assert result["available"] is False
    assert "cross-market/macro data unavailable for macro-required setup" in result["reasons"]
