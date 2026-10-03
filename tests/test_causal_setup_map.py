from causal_setup_map import resolve_causal_setup


def test_typed_relation_target_mapping():
    mapped = resolve_causal_setup(
        relation="SPURS_VOLATILITY",
        source_type="MACRO_INDICATOR",
        target_type="MARKET_STRUCTURE",
        source_name="US_CPI_SURPRISE",
        target_name="VOLATILITY_BREAKOUT",
    )
    assert mapped["eligible"] is True
    assert mapped["setup_id"] == "range_reversal_v1"
    assert mapped["method"] == "relation_target_type"
    assert mapped["execution_authority"] is False


def test_token_asian_breakout_mapping():
    mapped = resolve_causal_setup(
        relation="UNKNOWN_RELATION",
        source_type="SESSION",
        target_type="MARKET_STRUCTURE",
        source_name="ASIAN_RANGE",
        target_name="LONDON_BREAKOUT",
    )
    assert mapped["setup_id"] == "asian_range_breakout_v1"
    assert mapped["method"] == "token_asian_breakout"


def test_unclassified_when_no_typed_match():
    mapped = resolve_causal_setup(
        relation="WEIRD",
        source_type="OTHER",
        target_type="OTHER",
        source_name="FOO",
        target_name="BAR",
    )
    assert mapped["eligible"] is False
    assert mapped["setup_id"] == "unclassified_v1"
