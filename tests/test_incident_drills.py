from incident_drills import run


def test_incident_drills_are_safe_and_pass_without_broker_access():
    result = run()
    assert result["status"] == "PASS"
    assert result["passed"] is True
    assert result["execution_authority"] is False
    assert result["checks"]["kill_switch"]["passed"] is True
    assert result["checks"]["duplicate_intent"]["duplicate_blocked"] is True
    assert result["checks"]["provider_outage"]["deterministic_only"] is True
    assert result["checks"]["bad_market_data"]["passed"] is True
    assert result["checks"]["market_closed_filter"]["passed"] is True
    assert result["checks"]["calendar_news_outage"]["passed"] is True
    assert result["checks"]["restart_persistence"]["passed"] is True
