import concurrent.futures
import pytest
from core.trading_safety import TradingStore, PortfolioRiskArbiter

def test_atomic_risk_reservation_prevents_cap_breach(tmp_path):
    db_path = str(tmp_path / "test_risk.db")
    store = TradingStore(db_path)
    # Arbiter with max 3% open risk on $100,000 equity = max $3,000 open risk
    arbiter = PortfolioRiskArbiter(store, max_open_risk_fraction=0.03)
    equity = 100000.0
    risk_per_trade = 1000.0  # Each trade takes $1,000 risk -> max 3 trades allowed!

    # Launch 10 concurrent threads attempting to reserve $1,000 risk
    def try_reserve(i):
        # Must use atomic check-and-reserve
        if hasattr(arbiter, "permit_and_reserve"):
            return arbiter.permit_and_reserve(equity=equity, new_risk=risk_per_trade, reservation_id=f"order_{i}")
        else:
            # Fallback to non-atomic check
            return arbiter.permit(equity=equity, new_risk=risk_per_trade)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(try_reserve, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result()[0])

    # Exactly 3 trades should be permitted; 7 must be rejected!
    permitted_count = sum(1 for r in results if r is True)
    assert permitted_count == 3, f"Expected 3 permitted trades, but got {permitted_count} (cap breached!)"

    # Releasing one reservation frees up capacity for one more trade
    store.release_risk("order_0")
    # At least one reservation was order_0 if it was among permitted, or release any existing one:
    active_keys = list(store._risk_reservations.keys())
    assert len(active_keys) >= 2
    store.release_risk(active_keys[0])
    ok, _ = arbiter.permit_and_reserve(equity=equity, new_risk=risk_per_trade, reservation_id="order_extra")
    assert ok is True


def test_risk_reservation_ttl_expiry(tmp_path):
    import time
    db_path = str(tmp_path / "test_ttl.db")
    store = TradingStore(db_path)
    arbiter = PortfolioRiskArbiter(store, max_open_risk_fraction=0.03, max_correlated_risk_fraction=0.03)
    equity = 100000.0

    # Reserve full 3% risk with a very short TTL (0.1s)
    ok, _ = arbiter.permit_and_reserve(equity=equity, new_risk=3000.0, reservation_id="short_ttl", ttl_seconds=0.1)
    assert ok is True

    # Immediately, new risk should be rejected
    ok2, reason2 = arbiter.permit(equity=equity, new_risk=500.0)
    assert ok2 is False
    assert "portfolio open-risk limit" in reason2

    # After TTL expires, risk should be available again
    time.sleep(0.15)
    ok3, _ = arbiter.permit_and_reserve(equity=equity, new_risk=1000.0, reservation_id="after_expiry")
    assert ok3 is True


def test_record_open_trade_releases_reservation(tmp_path):
    from core.trading_safety import TradeProposal
    db_path = str(tmp_path / "test_trade_release.db")
    store = TradingStore(db_path)
    arbiter = PortfolioRiskArbiter(store, max_open_risk_fraction=0.03)
    equity = 100000.0

    proposal = TradeProposal(
        strategy="scalp",
        symbol="EURUSD",
        side="BUY",
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
        confidence=0.85,
        features={"setup_id": "test_setup"},
    )
    reservation_id = proposal.setup_key()

    # Reserve $2,000
    ok, _ = arbiter.permit_and_reserve(equity=equity, new_risk=2000.0, reservation_id=reservation_id)
    assert ok is True
    assert store.reserved_risk() == 2000.0
    assert store.open_risk() == 2000.0

    # Record the open trade with the same setup_key
    store.record_open_trade(
        ticket=12345,
        proposal=proposal,
        volume=0.1,
        risk_amount=2000.0,
        payload={"intent_key": reservation_id},
        provenance="mt5_broker",
    )

    # Reservation should be released, and trades table now holds the $2,000 without double counting!
    assert store.reserved_risk() == 0.0
    assert store.open_risk() == 2000.0
