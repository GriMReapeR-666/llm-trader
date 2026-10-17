import os, sys, tempfile
os.environ["QP_HOME"] = tempfile.mkdtemp(); os.environ["QP_MODE"] = "paper"
os.environ["TV_WEBHOOK_SECRET"] = "s3cret"; os.environ["QP_TV_AUTOEXEC"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from quantpipe import mcp_server as m, store, mt5_exec as ex
from quantpipe.strategy import EXAMPLE_EMA_RSI as SPEC


def test_pipeline_and_gate():
    m.create_synthetic_dataset("eur_h1", bars=12000)
    assert m.validate_strategy({**SPEC, "entry_long": [["nope", ">", 1]]})["valid"] is False
    assert m.save_strategy(SPEC)["version"] == 1
    r = m.backtest("eur_h1", strategy="ema_cross_rsi")
    assert r["metrics"]["trades"] > 20 and "flags" in r["diagnostics"]
    # costs matter: wide-spread broker must be worse than ECN
    r2 = m.backtest("eur_h1", broker="standard_mm", strategy="ema_cross_rsi")
    assert r2["metrics"]["total_costs"] > r["metrics"]["total_costs"]
    # holdout budget
    for _ in range(2): m.backtest("eur_h1", strategy="ema_cross_rsi", segment="holdout")
    assert "exhausted" in m.backtest("eur_h1", strategy="ema_cross_rsi", segment="holdout")["error"]
    # deploy gate blocks unvalidated strategy
    assert m.deploy_strategy("ema_cross_rsi")["deployed"] is False


def test_lot_rules_and_min_lot_skip():
    from quantpipe.models import load_brokers
    from quantpipe.backtest import size_position
    b = load_brokers()["us_retail_lowlev"]            # min lot 0.1
    lots, st = size_position(b, 1000, 1.1, 0.0050, 1.0)   # wants ~0.02 lots -> below min
    assert st == "below_min_lot"
    lots, st = size_position(load_brokers()["ecn_raw"], 10000, 1.1, 0.0050, 1.0)
    assert st == "ok" and abs(lots - 0.2) < 1e-9


def test_tv_webhook_paper_exec_and_risk_guard():
    from fastapi.testclient import TestClient
    from quantpipe import tv_webhook
    tv_webhook.SECRET = "s3cret"
    c = ex.get_client(); c.set_quote("EURUSD", 1.1000, 1.1002)
    t = TestClient(tv_webhook.app)
    assert t.post("/tv", json={"secret": "bad", "symbol": "X", "action": "buy"}).status_code == 401
    body = {"secret": "s3cret", "strategy": "tv_a", "symbol": "OANDA:EURUSD", "action": "buy", "sl": 1.0950, "tp": 1.1100, "risk_pct": 0.5}
    j = t.post("/tv", json=body).json()
    assert j["executed"]["ok"] and j["executed"]["mode"] == "paper"
    assert len(c.positions("EURUSD")) == 1
    # no SL -> blocked by RiskGuard
    j = t.post("/tv", json={**body, "sl": 0}).json()
    assert j["executed"]["reason"] == "stop_loss_required"
    # symbol whitelist
    c.set_quote("BTCUSD", 1, 1.1)
    assert ex.place("BTCUSD", "buy", 0.9, risk_pct=0.5)["reason"] == "symbol_not_allowed"
    # risk per trade cap
    assert ex.place("EURUSD", "buy", 1.09, risk_pct=3)["reason"] == "risk_per_trade_exceeds_limit"
    # kill switch blocks everything
    m.set_kill_switch(True)
    assert ex.place("EURUSD", "buy", 1.09, risk_pct=0.5)["reason"] == "kill_switch_on"
    m.set_kill_switch(False)
    assert t.post("/tv", json={**body, "action": "close"}).json()["executed"][0]["ok"]
    assert len(c.positions("EURUSD")) == 0


def test_macro_alignment_and_mql5():
    from quantpipe import events as E, mql5
    m.create_synthetic_events("ev"); m.create_synthetic_dataset("eur30k", bars=30000, seed=2)
    m.inject_event_shocks("eur30k", "ev", "eur_react", bps_per_z=8)
    macro = {"name": "macro_s", "symbol": "EURUSD", "timeframe": "H1", "indicators": {"atr": {"type": "atr", "period": 14}},
             "entry_long": [["ev_surprise_net", ">", 1.0], ["ev_mins_since", "<=", 90]],
             "entry_short": [["ev_surprise_net", "<", -1.0], ["ev_mins_since", "<=", 90]],
             "risk": {"risk_pct": 1, "sl_atr": 2, "tp_atr": 3, "atr_ind": "atr", "max_bars": 12}}
    # 1) the study must see the reaction on shocked data, and NOT on unshocked data (no lookahead leakage)
    on = m.event_study("eur_react", "ev", "EURUSD", "H1")["corr_surprise_vs_return"]["h1"]
    off = m.event_study("eur30k", "ev", "EURUSD", "H1")["corr_surprise_vs_return"]["h1"]
    assert on > 0.5 and abs(off) < 0.25
    # 2) strategy: edge only where reaction exists
    r_on = m.backtest("eur_react", strategy=None, spec=macro, segment="all", events="ev")["metrics"]
    r_off = m.backtest("eur30k", strategy=None, spec=macro, segment="all", events="ev")["metrics"]
    assert r_on["profit_factor"] > 1.05 > r_off["profit_factor"] * 1.05 or r_on["net_profit"] > r_off["net_profit"]
    # 3) event-dependent spec without events must fail loudly, never silently trade without fundamentals
    with pytest.raises(ValueError): m.backtest("eur30k", spec=macro, segment="all")
    # 4) point-in-time z-scores: first 4 releases of each series must have NaN z (no future std)
    e = E.load_events("ev"); first = e.groupby(["currency", "event"]).head(4)
    assert first.surprise_z.isna().all()
    # 5) MQL5 export is structurally sound and carries the event logic
    src = mql5.export_mq5(macro); assert src.count("{") == src.count("}") and "EvSurprise" in src and "%%" not in src
    # 6) server-time conversion handles DST
    import pandas as pd
    u = E.server_to_utc(pd.DatetimeIndex(["2023-01-10 10:00", "2023-07-10 10:00"]), "ny_close")
    assert [x.hour for x in u] == [8, 7]


def test_tester_helpers():
    from quantpipe import mt5_tester as T
    assert "Model=1" in T.build_ini("QP\\x", "EURUSD", "H1", "2022.01.01", "2023.01.01")
    assert T.parse_compile_log("a.mq5(1,2) : error 1: x\nResult: 1 errors, 0 warnings")["ok"] is False
    assert T.parse_report("<td>Total Net Profit:</td><td><b>1 234.50</b></td>")["net_profit"] == 1234.5
