"""Edge-gate tests: net-EV floor, BOCPD veto, heat cap, vol overlay,
calibrated P(win) model, and metrics-engine persistence."""
import json
import math
import sqlite3

import pytest

from core.trading_safety import DeterministicValidator, TradingStore, TradeProposal


def _make_store(tmp_path):
    return TradingStore(path=str(tmp_path / "edge.db"))


def _proposal():
    return TradeProposal(
        strategy="swing", symbol="EURUSD", side="BUY",
        entry=1.1000, stop_loss=1.0950, take_profit=1.1150,
        confidence=0.8,
        features={"setup_id": "test_v1", "regime": "TRENDING",
                  "ema_alignment": 1, "mtf_alignment": 1, "adx": 30,
                  "near_key_level": True, "confirmed_candle": True,
                  "volume_confirmed": True},
    )


def _market(**extra):
    env = {
        "market_open": True, "tick_age_ms": 10, "max_tick_age_ms": 5000,
        "spread": 0.0001, "max_spread": 0.001, "clock_ok": True,
        "symbol_tradable": True, "session_allowed": True,
        "liquidity_ok": True, "microstructure_ok": True,
        "required_data_ok": True, "completed_candle": True,
        "stop_distance": 0.005, "min_stop_distance": 0.0001,
        "entry_tolerance": 1.0, "high_impact_news": False,
    }
    env.update(extra)
    return env


def _seed_trades(db_path, n, r_values):
    """Insert closed labelled trades into the REAL TradingStore schema.

    Fully schema-driven: every NOT NULL column is satisfied from a value map
    with type-aware defaults, so journal migrations cannot break the fixture.
    Only rows the validator queries are populated (closed + labelled).
    """
    con = sqlite3.connect(db_path)
    # A bare TradingStore journal has trades but NOT trade_labels; create the
    # label table when absent so the fixture mirrors production databases.
    con.execute("""CREATE TABLE IF NOT EXISTS trade_labels (
        ticket INTEGER PRIMARY KEY, label TEXT NOT NULL, rationale TEXT NOT NULL,
        r_multiple REAL, created_at TEXT NOT NULL, label_rule TEXT,
        review_status TEXT NOT NULL DEFAULT 'AUTO')""")
    # Broker evidence the net-EV gate requires: `_rolling_net_ev` only admits
    # tickets with an independent submission record (`order_intents.mt5_ticket`
    # or a broker execution report). Without these rows the seeded trades are
    # correctly excluded as `unbrokered_trade` and the gate cannot engage --
    # which is exactly what this fixture must avoid when testing the gate.
    con.execute("""CREATE TABLE IF NOT EXISTS order_intents (
        intent_key TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        run_id TEXT, proposal_id TEXT, status TEXT NOT NULL, mt5_ticket INTEGER,
        payload TEXT NOT NULL, result TEXT, error TEXT)""")
    columns = con.execute("PRAGMA table_info(trades)").fetchall()
    named = {row[1]: row for row in columns}
    if not columns:
        con.execute("""CREATE TABLE IF NOT EXISTS trades (
            ticket INTEGER PRIMARY KEY, magic INTEGER NOT NULL DEFAULT 460001,
            strategy TEXT NOT NULL DEFAULT 'swing', symbol TEXT NOT NULL DEFAULT '',
            side TEXT, setup_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
            opened_at TEXT, closed_at TEXT, pnl REAL)""")
        columns = con.execute("PRAGMA table_info(trades)").fetchall()
        named = {row[1]: row for row in columns}
    base = {
        "ticket": None,  # filled per row below
        "magic": 460001,
        "strategy": "swing",
        "symbol": "EURUSD",
        "side": "BUY",
        "setup_id": "test_v1",
        "payload": "{}",
        "opened_at": "2026-01-01T00:00:00+00:00",
        "pnl": 0.0,
    }
    type_defaults = {"TEXT": "", "REAL": 0.0, "INTEGER": 0}
    insert_cols = []
    for name, row in named.items():
        if row[5]:  # primary key column -> assigned explicitly
            continue
        col_type = (row[2] or "TEXT").upper()
        base[name] = base.get(name, type_defaults.get(col_type.split("(")[0], ""))
        insert_cols.append(name)
    pk = [row[1] for row in columns if row[5]]
    insert_cols.extend(pk)
    placeholders = ",".join("?" for _ in insert_cols)
    labels_cols = {row[1] for row in con.execute("PRAGMA table_info(trade_labels)").fetchall()}
    for i in range(n):
        r = r_values[i % len(r_values)]
        values = dict(base)
        values["ticket"] = 1000 + i
        values["closed_at"] = f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00"
        values["pnl"] = r * 100.0
        # Direct-SQL seed: the journal trigger now requires a declared source.
        if "provenance" in values:
            values["provenance"] = "mt5_broker"
        con.execute(
            f"INSERT OR REPLACE INTO trades({','.join(insert_cols)}) VALUES({placeholders})",
            tuple(values[c] for c in insert_cols))
        label_row = {
            "ticket": 1000 + i,
            "label": "WIN_TARGET" if r > 0 else "LOSS_STOP",
            "rationale": "test",
            "r_multiple": r,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        lcols = [c for c in ("ticket", "label", "rationale", "r_multiple", "created_at")
                 if c in labels_cols]
        con.execute(
            f"INSERT OR REPLACE INTO trade_labels({','.join(lcols)}) VALUES({','.join('?' for _ in lcols)})",
            tuple(label_row[c] for c in lcols))
        con.execute(
            "INSERT OR REPLACE INTO order_intents(intent_key,created_at,updated_at,"
            "run_id,proposal_id,status,mt5_ticket,payload,result,error)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"intent-{values["ticket"]}", values["closed_at"], values["closed_at"],
             "run", "proposal", "FILLED", values["ticket"], "{}", "{}", ""))
    con.commit()
    con.close()


class TestNetExpectancyFloor:
    def test_cold_start_passes_without_labelled_trades(self, tmp_path):
        store = _make_store(tmp_path)
        validator = DeterministicValidator(store=store)
        result = validator.validate(_proposal(), _market())
        assert result.approved, result.reasons

    def test_negative_net_edge_blocks_after_enough_samples(self, tmp_path):
        db_path = str(tmp_path / "neg.db")
        store = TradingStore(path=db_path)
        _seed_trades(db_path, 40, [-1.0])
        validator = DeterministicValidator(store=store, min_ev_samples=30)
        market = _market(friction_r=0.05)
        result = validator.validate(_proposal(), market)
        assert not result.approved
        assert any("net expectancy" in reason for reason in result.reasons)

    def test_positive_edge_passes(self, tmp_path):
        db_path = str(tmp_path / "pos.db")
        store = TradingStore(path=db_path)
        _seed_trades(db_path, 40, [2.0, 2.0, -1.0])
        validator = DeterministicValidator(store=store, min_ev_samples=30)
        result = validator.validate(_proposal(), _market(friction_r=0.05))
        assert result.approved, result.reasons

    def test_corrupt_journal_degrades_to_pass(self):
        from unittest.mock import MagicMock
        store = MagicMock()
        store.is_kill_switch_active.return_value = False
        store.setup_seen_recently.return_value = False
        store._connect.side_effect = RuntimeError("boom")
        validator = DeterministicValidator(store=store)
        result = validator.validate(_proposal(), _market())
        assert result.approved


class TestChangepointVeto:
    def test_high_changepoint_probability_vetoes(self, tmp_path):
        store = _make_store(tmp_path)
        validator = DeterministicValidator(store=store)
        result = validator.validate(
            _proposal(), _market(changepoint_probability=0.9))
        assert not result.approved
        assert any("changepoint" in reason for reason in result.reasons)

    def test_absent_key_is_backwards_compatible(self, tmp_path):
        store = _make_store(tmp_path)
        validator = DeterministicValidator(store=store)
        result = validator.validate(_proposal(), _market())
        assert result.approved

    def test_low_probability_passes(self, tmp_path):
        store = _make_store(tmp_path)
        validator = DeterministicValidator(store=store)
        result = validator.validate(
            _proposal(), _market(changepoint_probability=0.05))
        assert result.approved


class TestPortfolioHeatCap:
    def test_heat_budget_setter_bounded(self):
        from core.risk_manager import RiskManager
        risk = RiskManager(balance=10000.0)
        risk.set_heat_budget_r(1.5)
        assert risk.max_portfolio_heat_r == 1.5
        risk.set_heat_budget_r(99.0)  # out of bounds -> unchanged
        assert risk.max_portfolio_heat_r == 1.5

    def test_heat_unmeasurable_paper_mode_allows_sizing(self):
        from core.risk_manager import RiskManager
        import core.risk_manager as rm_module
        risk = RiskManager(balance=10000.0)
        saved_mt5 = rm_module.mt5
        try:
            rm_module.mt5 = None  # paper install: heat cannot be measured
            vol = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9)
            assert vol >= 0.0
            assert risk.check_portfolio_heat() is True
        finally:
            rm_module.mt5 = saved_mt5

    def test_vol_target_overlay_bounds_multiplier(self):
        from core.risk_manager import RiskManager
        risk = RiskManager(balance=10000.0)
        base = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9)
        quiet = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9,
                                        atr=0.0005, atr_target=0.002)
        loud = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9,
                                       atr=0.004, atr_target=0.002)
        if base > 0:
            assert quiet >= base * 1.9 or quiet == pytest.approx(base * 2.0, abs=0.02)
            assert loud <= base * 0.6


class TestWinProbabilityModel:
    def _seed_model_store(self, db_path, n=120):
        """Two separable regimes: high-ADX setups win, low-ADX setups lose."""
        con = sqlite3.connect(db_path)
        con.execute("""CREATE TABLE IF NOT EXISTS trades (
            ticket INTEGER PRIMARY KEY, symbol TEXT, side TEXT, strategy TEXT,
            setup_id TEXT, payload TEXT NOT NULL DEFAULT '{}',
            opened_at TEXT, closed_at TEXT, pnl REAL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS trade_labels (
            ticket INTEGER PRIMARY KEY, label TEXT NOT NULL,
            rationale TEXT NOT NULL, r_multiple REAL, created_at TEXT NOT NULL,
            label_rule TEXT, review_status TEXT NOT NULL DEFAULT 'AUTO')""")
        for i in range(n):
            adx = 35.0 + (i % 5) if i % 2 == 0 else 12.0 + (i % 4)
            features = {"adx": adx, "rsi_14": 40.0, "ema_alignment": 1,
                        "mtf_alignment": 1, "atr_percentile": 50.0,
                        "hurst_exponent": 0.55, "spread_pips": 1.0}
            payload = json.dumps({"features": features})
            win = adx > 25
            label = "WIN_TARGET" if win else "LOSS_STOP"
            con.execute(
                """INSERT OR REPLACE INTO trades
                   (ticket,closed_at,pnl,payload,side) VALUES(?,?,?,?,?)""",
                (2000 + i, f"2026-02-{(i % 27) + 1:02d}T00:00:00+00:00",
                 (1.8 if win else -1.0) * 100.0, payload, "BUY"))
            con.execute(
                """INSERT OR REPLACE INTO trade_labels
                   (ticket,label,rationale,r_multiple,created_at)
                   VALUES(?,?,?,?,?)""",
                (2000 + i, label, "test", 1.8 if win else -1.0,
                 "2026-02-01T00:00:00+00:00"))
        con.commit()
        con.close()

    def test_trains_and_calibrates_on_separable_data(self, tmp_path):
        from intelligence.win_probability_model import WinProbabilityModel
        db_path = str(tmp_path / "wp.db")
        self._seed_model_store(db_path)
        model = WinProbabilityModel.train_from_store(db_path, min_samples=30)
        assert model is not None
        good = model.predict_calibrated({"adx": 38, "rsi_14": 40, "ema_alignment": 1,
                                         "mtf_alignment": 1, "atr_percentile": 50,
                                         "hurst_exponent": 0.55, "spread_pips": 1})
        bad = model.predict_calibrated({"adx": 10, "rsi_14": 40, "ema_alignment": 1,
                                        "mtf_alignment": 1, "atr_percentile": 50,
                                        "hurst_exponent": 0.55, "spread_pips": 1})
        assert good is not None and bad is not None
        assert good > bad
        assert 0.01 <= good <= 0.99 and 0.01 <= bad <= 0.99

    def test_insufficient_samples_returns_none(self, tmp_path):
        from intelligence.win_probability_model import WinProbabilityModel
        db_path = str(tmp_path / "tiny.db")
        self._seed_model_store(db_path, n=6)
        assert WinProbabilityModel.train_from_store(db_path, min_samples=30) is None

    def test_incomplete_features_return_none(self, tmp_path):
        from intelligence.win_probability_model import WinProbabilityModel
        db_path = str(tmp_path / "wp2.db")
        self._seed_model_store(db_path)
        model = WinProbabilityModel.train_from_store(db_path, min_samples=30)
        if model is not None:
            assert model.predict_calibrated({"adx": 30}) is None


class TestMetricsEnginePersistence:
    def test_round_trip_preserves_counters(self):
        from core.metrics import QuantitativeMetricsEngine
        engine = QuantitativeMetricsEngine(initial_balance=10000.0)
        for pnl, risk in [(180.0, 100.0), (-100.0, 100.0), (-95.0, 100.0)]:
            engine.record_trade(pnl, risk_amount=risk)
            engine.update_equity(10000.0 + pnl)
        state = engine.to_state()
        revived = QuantitativeMetricsEngine.from_state(state)
        assert revived.r_multiples == pytest.approx(engine.r_multiples)
        assert revived.consecutive_losses == engine.consecutive_losses
        assert revived.max_drawdown_pct == pytest.approx(engine.max_drawdown_pct)

    def test_record_trade_prefers_actual_risk_amount(self):
        from core.metrics import QuantitativeMetricsEngine
        engine = QuantitativeMetricsEngine(initial_balance=10000.0)
        # $50 loss on a $200 planned risk = -0.25R (NOT the legacy -0.5R).
        engine.record_trade(-50.0, risk_amount=200.0)
        assert engine.r_multiples[-1] == pytest.approx(-0.25)

        from core.risk_manager import RiskManager
        risk = RiskManager(balance=10000.0)
        base = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9)
        quiet = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9,
                                        atr=0.0005, atr_target=0.002)
        loud = risk.calc_position_size("EURUSD", 1.1000, 1.0950, confidence=0.9,
                                       atr=0.004, atr_target=0.002)
        if base > 0:
            assert quiet >= base * 1.9 or quiet == pytest.approx(base * 2.0, abs=0.02)
            assert loud <= base * 0.6
