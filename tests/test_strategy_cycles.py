from mt5_trader import resolve_strategy_cycles


def test_both_explicit_lower_timeframe_keeps_independent_swing_engine():
    assert resolve_strategy_cycles("M15", "both") == [("swing", "H1"), ("scalp", "M15")]


def test_both_explicit_higher_timeframe_keeps_independent_scalp_engine():
    assert resolve_strategy_cycles("H4", "both") == [("swing", "H4"), ("scalp", "M15")]


def test_single_strategy_auto_has_a_deterministic_default_timeframe():
    assert resolve_strategy_cycles("AUTO", "swing") == [("swing", "H1")]
    assert resolve_strategy_cycles("AUTO", "scalp") == [("scalp", "M15")]
