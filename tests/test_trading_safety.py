from core.trading_safety import DeterministicValidator, TradeProposal, TradingStore, safe_risk_fraction


def _proposal():
    return TradeProposal(
        strategy="scalp", symbol="EURUSD", side="BUY", entry=1.1000,
        stop_loss=1.0900, take_profit=1.1200, confidence=.8,
        features={"ema_alignment": 1, "mtf_alignment": 1, "adx": 25,
                  "near_key_level": True, "confirmed_candle": True,
                  "volume_confirmed": True},
    )


def _market():
    return {"market_open": True, "tick_age_ms": 10, "max_tick_age_ms": 5000,
            "symbol_tradable": True, "spread": .0001, "max_spread": .001,
            "completed_candle": True, "breakout_event": False,
            "stop_distance": .01, "min_stop_distance": .0001,
            "high_impact_news": False, "setup_cooldown_seconds": 0}


def test_percent_input_is_normalized_and_capped():
    assert safe_risk_fraction(1.0) == .01
    assert safe_risk_fraction(.01) == .01
    assert safe_risk_fraction(5.0) == .01


def test_validator_fails_closed_and_accepts_complete_safe_setup(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    validator = DeterministicValidator(store)
    accepted = validator.validate(_proposal(), _market())
    assert accepted.approved
    assert accepted.rule_trace["spread_within_limit"]["passed"] is True
    assert accepted.rule_trace["reward_risk_valid"]["passed"] is True
    assert all("passed" in rule for rule in accepted.rule_trace.values())
    store.log_proposal(_proposal(), accepted)
    with store._connect() as con:
        payload = con.execute("SELECT payload FROM proposals ORDER BY id DESC LIMIT 1").fetchone()["payload"]
    assert "validation_trace" in payload
    stale = _market()
    stale["tick_age_ms"] = 10_000
    rejected = validator.validate(_proposal(), stale)
    assert not rejected.approved
    assert "stale tick" in rejected.reasons
    assert rejected.rule_trace["tick_fresh"]["passed"] is False


def test_kill_switch_rejects_trades(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    store.set_kill_switch(True, "test")
    result = DeterministicValidator(store).validate(_proposal(), _market())
    assert not result.approved
    assert "kill switch active" in result.reasons


def test_missing_event_and_data_evidence_cannot_be_overridden_by_paper_quote_mechanics(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    market = _market()
    market.update({
        "execution_price": 1.1,
        "entry_tolerance": 1.0,
        "min_stop_distance": 0.0,
        "required_data_ok": False,
        "required_data_reasons": ["calendar unavailable"],
        "high_impact_news": True,
    })
    result = DeterministicValidator(store).validate(_proposal(), market)
    assert not result.approved
    assert "calendar unavailable" in result.reasons
    assert "news data missing or high-impact event" in result.reasons


def test_portfolio_risk_arbiter_input_validation_and_drawdown_gate(tmp_path):
    from core.trading_safety import PortfolioRiskArbiter
    store = TradingStore(str(tmp_path / "state.db"))
    arbiter = PortfolioRiskArbiter(store, max_drawdown_fraction=0.10)

    # Valid request
    ok, reason = arbiter.permit(10000.0, 100.0)
    assert ok is True
    assert reason == "approved"

    # Non-finite equity
    ok, reason = arbiter.permit(float("nan"), 100.0)
    assert ok is False
    assert "non-finite" in reason or "invalid" in reason

    # Negative / zero equity
    ok, reason = arbiter.permit(0.0, 100.0)
    assert ok is False
    assert "out-of-bounds" in reason

    # Negative risk
    ok, reason = arbiter.permit(10000.0, -50.0)
    assert ok is False
    assert "out-of-bounds" in reason

    # Drawdown kill switch check: peak set to 10000, equity drops to 8900 (11% DD >= 10%)
    ok, reason = arbiter.permit(8900.0, 100.0)
    assert ok is False
    assert "maximum drawdown" in reason
    assert store.is_kill_switch_active() is True


def test_update_state_is_atomic_read_modify_write(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    store.set_state("counter", 0)

    def bump(current):
        return int(current or 0) + 1

    assert store.update_state("counter", bump, default=0) == 1
    assert store.update_state("counter", bump, default=0) == 2
    assert store.get_state("counter") == 2


def test_setup_transition_uses_immediate_lock(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    proposal = _proposal()
    instance_id = store.record_setup_instance(proposal)
    store.transition_setup_instance(instance_id, "VALIDATED", reason="ok")
    store.transition_setup_instance(instance_id, "ACCEPTED", reason="permitted")
    row = store.latest_setup_instance(proposal.setup_key())
    assert row["state"] == "ACCEPTED"


def test_lag_confidence_gate_rejects_decayed_quotes(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    market = _market()
    market["lag_confidence"] = 0.1
    market["min_lag_confidence"] = 0.35
    result = DeterministicValidator(store).validate(_proposal(), market)
    assert not result.approved
    assert "lag confidence decayed below threshold" in result.reasons
    assert result.rule_trace["lag_confidence_ok"]["passed"] is False


def test_lag_confidence_gate_passes_fresh_quotes(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    market = _market()
    market["lag_confidence"] = 0.9
    result = DeterministicValidator(store).validate(_proposal(), market)
    assert result.approved
    assert result.rule_trace["lag_confidence_ok"]["passed"] is True


def test_vpin_toxicity_shield_rejects_toxic_adverse_selection(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    validator = DeterministicValidator(store)
    prop = _proposal()
    prop.features["vpin_toxicity"] = 0.82
    result = validator.validate(prop, _market())
    assert not result.approved
    assert "vpin toxicity too high" in result.reasons
    assert result.rule_trace["vpin_toxicity_acceptable"]["passed"] is False


def test_phantom_liquidity_shield_rejects_quote_stuffing(tmp_path):
    store = TradingStore(str(tmp_path / "state.db"))
    validator = DeterministicValidator(store)
    prop = _proposal()
    prop.features["phantom_liquidity_flag"] = True
    result = validator.validate(prop, _market())
    assert not result.approved
    assert "phantom liquidity / quote stuffing detected" in result.reasons
    assert result.rule_trace["phantom_liquidity_clear"]["passed"] is False

