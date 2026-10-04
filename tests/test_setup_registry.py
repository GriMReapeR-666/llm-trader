from research.setup_registry import classify


def test_malformed_setup_features_fail_closed_without_exception():
    result = classify({"adx": "not-a-number"}, "LONDON")
    assert result["setup_id"] == "unclassified_v1"
    assert result["eligible"] is False
    assert "malformed ADX" in result["reason"]


def test_nonfinite_or_negative_adx_fails_closed():
    for value in (float("nan"), float("inf"), -1):
        result = classify({"adx": value}, "LONDON")
        assert result["setup_id"] == "unclassified_v1"
        assert result["eligible"] is False
        assert "invalid ADX" in result["reason"]


def test_valid_registered_setup_still_classifies():
    result = classify({
        "adx": 25,
        "ema_alignment": 1,
        "mtf_alignment": 1,
        "near_key_level": True,
        "confirmed_candle": True,
    }, "LONDON")
    assert result["setup_id"] == "trend_pullback_v1"
    assert result["eligible"] is True


def test_momentum_cascade_v1_classification():
    result = classify({
        "adx": 26,
        "hawkes_cascade_active": True,
        "volume_confirmed": True,
        "confirmed_candle": True,
        "vpin_toxicity": 0.35,
    }, "LONDON")
    assert result["setup_id"] == "momentum_cascade_v1"
    assert result["eligible"] is True
    assert result["spec"]["min_rr"] == 2.0


def test_momentum_cascade_rejects_toxic_vpin():
    result = classify({
        "adx": 26,
        "hawkes_cascade_active": True,
        "volume_confirmed": True,
        "confirmed_candle": True,
        "vpin_toxicity": 0.85,
    }, "LONDON")
    assert result["setup_id"] == "unclassified_v1"

