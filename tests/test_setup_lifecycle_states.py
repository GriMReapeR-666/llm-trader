import sqlite3

from core.trading_safety import TradeProposal, TradingStore


def test_setup_instance_tracks_fill_management_partial_and_close(tmp_path):
    path = str(tmp_path / "lifecycle.db")
    store = TradingStore(path)
    proposal = TradeProposal(
        "scalp", "EURUSD", "BUY", 1.10, 1.09, 1.12, 0.8,
        {"setup_id": "trend_pullback_v1"},
    )
    instance_id = store.record_setup_instance(proposal)
    store.transition_setup_instance(instance_id, "VALIDATED")
    store.transition_setup_instance(instance_id, "ACCEPTED")
    store.record_open_trade(501, proposal, 1.0, 10.0, {"proposal_features": proposal.features}, provenance="mt5_broker")
    store.transition_setup_for_ticket(501, "PARTIAL", reason="partial close")
    store.record_close(501, 1.115, 15.0, "target")

    with sqlite3.connect(path) as con:
        row = con.execute("SELECT state,ticket FROM setup_instances WHERE id=?", (instance_id,)).fetchone()
        events = [item[0] for item in con.execute(
            "SELECT event_type FROM trade_lifecycle_events WHERE ticket=? ORDER BY id", (501,)
        ).fetchall()]
    assert row == ("CLOSED", 501)
    assert "FILLED" in events
    assert "CLOSED" in events


def test_setup_state_machine_rejects_terminal_to_management_transition(tmp_path):
    store = TradingStore(str(tmp_path / "terminal.db"))
    proposal = TradeProposal(
        "swing", "GBPJPY", "SELL", 190.0, 191.0, 188.0, 0.7,
        {"setup_id": "trend_pullback_v1"},
    )
    instance_id = store.record_setup_instance(proposal)
    store.transition_setup_instance(instance_id, "REJECTED")
    try:
        store.transition_setup_instance(instance_id, "MANAGED")
    except ValueError as exc:
        assert "invalid setup transition" in str(exc)
    else:
        raise AssertionError("terminal setup state accepted a management transition")
