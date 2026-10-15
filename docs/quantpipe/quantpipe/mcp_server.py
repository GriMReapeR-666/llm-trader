"""MCP server: strategy creation -> backtest -> stress -> optimise -> walk-forward -> diagnostics -> deploy,
plus TradingView signals and MT5 execution. Run: python -m quantpipe.mcp_server  (stdio)"""
from __future__ import annotations
import json
from mcp.server.fastmcp import FastMCP
from . import store, data as D, strategy as S, mt5_exec as ex
from .models import load_brokers, SCENARIOS, Scenario
from .backtest import run_backtest
from . import analysis as A
from . import events as E, mql5 as MQ, mt5_tester as TST

mcp = FastMCP("quantpipe")
HOLDOUT_MAX_USES = 2


def _clean(o):
    return json.loads(json.dumps(o, default=lambda x: float(x) if hasattr(x, "__float__") else str(x)))


def _spec(strategy: str | None, spec: dict | None, version: int | None = None):
    if spec is not None:
        errs = S.validate(spec)
        if errs: raise ValueError(f"invalid spec: {errs}")
        return spec, None
    s, v = store.get_strategy(strategy, version); return s, v


def _ev(name):
    return E.load_events(name) if name else None


def _scn(name_or_dict):
    if isinstance(name_or_dict, dict): return Scenario(**{"name": "custom", **name_or_dict})
    return SCENARIOS[name_or_dict or "baseline"]


# ---------- reference ----------
@mcp.tool()
def get_spec_schema() -> dict:
    """Strategy DSL reference + an example spec. Read this before creating strategies."""
    return {"schema": S.SPEC_DOC, "example": S.EXAMPLE_EMA_RSI, "scenarios": {k: v.to_dict() for k, v in SCENARIOS.items()}}


@mcp.tool()
def list_brokers() -> dict:
    """Broker constraint profiles (spread, commission, leverage, lot rules, stops level, swap...)."""
    return {k: v.to_dict() for k, v in load_brokers().items()}


@mcp.tool()
def add_broker_profile(name: str, profile: dict) -> str:
    """Add/override a broker profile (e.g. copy real values from MT5 symbol specs). Persisted to qp_data/brokers.json."""
    from .models import HOME, BrokerProfile
    BrokerProfile(name=name, **profile)  # validate fields
    p = HOME / "brokers.json"; cur = json.loads(p.read_text()) if p.exists() else {}
    cur[name] = profile; p.write_text(json.dumps(cur, indent=1)); return f"saved broker '{name}'"


# ---------- data ----------
@mcp.tool()
def create_synthetic_dataset(name: str, symbol: str = "EURUSD", timeframe: str = "H1", bars: int = 20000, seed: int = 1, start_price: float = 1.10) -> dict:
    """Generate regime-switching synthetic OHLC (for pipeline testing only, NOT evidence of real edge)."""
    df = D.synthetic(symbol, timeframe, bars, seed, start_price); D.save_dataset(name, df, {"symbol": symbol, "tf": timeframe, "synthetic": True})
    return {"name": name, "bars": len(df), "start": str(df.index[0]), "end": str(df.index[-1])}


@mcp.tool()
def download_mt5_dataset(name: str, symbol: str, timeframe: str, start: str, end: str) -> dict:
    """Download historical bars from the connected MT5 terminal (Windows). Dates ISO e.g. 2022-01-01."""
    df = D.fetch_mt5(symbol, timeframe, start, end); D.save_dataset(name, df, {"symbol": symbol, "tf": timeframe})
    return {"name": name, "bars": len(df), "start": str(df.index[0]), "end": str(df.index[-1])}


@mcp.tool()
def import_csv_dataset(name: str, path: str) -> dict:
    """Import OHLC CSV (MT5 export or generic: time,open,high,low,close)."""
    return D.import_csv(name, path)


@mcp.tool()
def list_datasets() -> list: return D.list_datasets()


# ---------- strategies ----------
@mcp.tool()
def validate_strategy(spec: dict) -> dict:
    """Validate a strategy spec without saving."""
    e = S.validate(spec); return {"valid": not e, "errors": e}


@mcp.tool()
def save_strategy(spec: dict, notes: str = "") -> dict:
    """Save a new version of a strategy (name taken from spec['name']). Put the hypothesis / change rationale in notes."""
    e = S.validate(spec)
    if e: return {"saved": False, "errors": e}
    return {"saved": True, "name": spec["name"], "version": store.save_strategy(spec["name"], spec, notes)}


@mcp.tool()
def get_strategy(name: str, version: int | None = None) -> dict:
    spec, v = store.get_strategy(name, version); return {"version": v, "spec": spec}


@mcp.tool()
def list_strategies() -> list: return store.list_strategies()


# ---------- backtesting ----------
def _prep(dataset, segment, spec_symbol_check=None):
    df = D.load_dataset(dataset); return D.split(df, segment)


@mcp.tool()
def backtest(dataset: str, broker: str = "ecn_raw", scenario: str | dict = "baseline", strategy: str | None = None,
             spec: dict | None = None, version: int | None = None, segment: str = "train", initial_balance: float = 10000.0,
             events: str | None = None) -> dict:
    """Backtest a saved strategy (name) or inline spec on a dataset with a broker profile + market scenario.
    segment: train (first 70%, default - use for iterating) | holdout (last 30%, limited uses - final validation only) | all.
    Returns metrics, diagnostics (flags explain what to fix), run_id."""
    sp, v = _spec(strategy, spec, version); name = sp["name"]
    if segment == "holdout":
        used = store.holdout_uses(name, dataset)
        if used >= HOLDOUT_MAX_USES: return {"error": f"holdout budget exhausted ({used}/{HOLDOUT_MAX_USES}) for {name} on {dataset}. Create a new dataset to validate further."}
        store.kv_set(f"holdout:{name}:{dataset}", used + 1)
    df, start = _prep(dataset, segment)
    r = run_backtest(df, sp, load_brokers()[broker], _scn(scenario), initial_balance, start, events=_ev(events))
    diag = A.diagnostics(r["trades"], r["metrics"])
    run = {"kind": "backtest", "strategy": name, "version": v, "spec": sp, "dataset": dataset, "segment": segment, "broker": broker,
           "scenario": r["scenario"], "events": events, "metrics": _clean(r["metrics"]), "diagnostics": _clean(diag), "trades": r["trades"]}
    rid = store.save_run(run)
    return {"run_id": rid, "metrics": run["metrics"], "diagnostics": run["diagnostics"], "recent_trades": r["trades"][-5:],
            **({"holdout_uses": f"{store.holdout_uses(name, dataset)}/{HOLDOUT_MAX_USES}"} if segment == "holdout" else {})}


@mcp.tool()
def stress_test(dataset: str, strategy: str | None = None, spec: dict | None = None, brokers: list[str] | None = None,
                scenarios: list[str] | None = None, segment: str = "train", events: str | None = None) -> dict:
    """Run strategy across brokers x market scenarios; reports robustness (profitable cells, worst case)."""
    sp, v = _spec(strategy, spec); B = load_brokers(); df, start = _prep(dataset, segment)
    res = A.stress_matrix(df, sp, {k: B[k] for k in (brokers or B)}, {k: SCENARIOS[k] for k in (scenarios or SCENARIOS)}, start, events=_ev(events))
    rid = store.save_run({"kind": "stress", "strategy": sp["name"], "version": v, "dataset": dataset, "segment": segment, "result": _clean(res), "spec": sp})
    return {"run_id": rid, **_clean(res)}


@mcp.tool()
def optimize_params(dataset: str, grid: dict, strategy: str | None = None, spec: dict | None = None, broker: str = "ecn_raw",
                    scenario: str = "baseline", metric: str = "sharpe", min_trades: int = 30, max_combos: int = 150, events: str | None = None) -> dict:
    """Grid search. grid = {"indicators.fast.period":[10,20], "risk.sl_atr":[1.5,2,3]} (dotted paths into spec).
    Runs on TRAIN segment only. ALWAYS follow with walk_forward_test."""
    sp, _ = _spec(strategy, spec); df, start = _prep(dataset, "train")
    return _clean(A.optimize(df, sp, grid, load_brokers()[broker], SCENARIOS[scenario], metric, min_trades, max_combos, start_idx=300, events=_ev(events)))


@mcp.tool()
def walk_forward_test(dataset: str, grid: dict, strategy: str | None = None, spec: dict | None = None, broker: str = "ecn_raw",
                      scenario: str = "baseline", folds: int = 4, metric: str = "sharpe", events: str | None = None) -> dict:
    """Rolling walk-forward on TRAIN segment: optimise in-sample, test out-of-sample each fold. verdict PASS/FAIL."""
    sp, _ = _spec(strategy, spec); df, _ = _prep(dataset, "train")
    return _clean(A.walk_forward(df, sp, grid, load_brokers()[broker], SCENARIOS[scenario], folds, metric=metric, events=_ev(events)))


@mcp.tool()
def get_run(run_id: str, include_trades: bool = False, last_n_trades: int = 20) -> dict:
    r = store.load_run(run_id)
    if not include_trades: r.pop("trades", None)
    else: r["trades"] = r["trades"][-last_n_trades:]
    return r


@mcp.tool()
def list_runs(strategy: str | None = None, n: int = 20) -> list:
    """Experiment ledger - compare iterations to see whether changes actually helped."""
    return store.list_runs(strategy, n)


@mcp.tool()
def compare_runs(run_ids: list[str]) -> list:
    """Side-by-side metrics + spec diffs (indicators/entries/risk) for several runs."""
    out = []
    for rid in run_ids:
        r = store.load_run(rid); sp = r.get("spec", {})
        out.append({"run_id": rid, "version": r.get("version"), "broker": r.get("broker"), "scenario": r.get("scenario"),
                    "segment": r.get("segment"), "metrics": {k: r.get("metrics", {}).get(k) for k in ("net_profit", "max_drawdown_pct", "sharpe", "profit_factor", "win_rate_pct", "trades", "expectancy_r", "total_costs")},
                    "flags": r.get("diagnostics", {}).get("flags"), "spec_core": {k: sp.get(k) for k in ("indicators", "entry_long", "entry_short", "filters", "risk")}})
    return out


# ---------- economic events / macro ----------
@mcp.tool()
def import_events(name: str, path: str, server_tz: str = "utc") -> dict:
    """Import an economic-calendar CSV (time,currency,event,impact,actual,forecast,previous). server_tz: utc | fixed:+2 | ny_close
    (use ny_close for the MT5 ExportCalendar.mq5 output of typical brokers). Computes point-in-time surprise z-scores."""
    return E.import_events_csv(name, path, server_tz)


@mcp.tool()
def create_synthetic_events(name: str, start: str = "2020-01-01", end: str = "2024-12-31", seed: int = 3) -> dict:
    """Synthetic NFP/CPI/FOMC/ECB-style calendar for pipeline tests only."""
    E.save_events(name, E.synthetic_events(start, end, seed)); ev = E.load_events(name)
    return {"name": name, "events": len(ev), "with_surprise": int(ev.surprise_z.notna().sum())}


@mcp.tool()
def list_event_sets() -> list: return E.list_event_sets()


@mcp.tool()
def inject_event_shocks(dataset: str, events: str, new_dataset: str, symbol: str = "EURUSD", timeframe: str = "H1", bps_per_z: float = 8.0) -> dict:
    """TEST ONLY: create a dataset whose prices react to surprises, to verify that macro strategies/alignment work end-to-end."""
    df = E.apply_event_shocks(D.load_dataset(dataset), E.load_events(events), symbol, timeframe, bps_per_z)
    D.save_dataset(new_dataset, df, {"shocked": True}); return {"name": new_dataset, "bars": len(df)}


@mcp.tool()
def event_study(dataset: str, events: str, symbol: str, timeframe: str, event: str | None = None) -> dict:
    """Average post-release return (bps) by surprise bucket + surprise/return correlation. Run this BEFORE building a macro
    strategy: if surprises don't move your instrument in your data, there is nothing to trade."""
    return _clean(E.event_study(D.load_dataset(dataset), E.load_events(events), symbol, timeframe, event))


@mcp.tool()
def event_replay(run_id: str, events: str, pre_hours: float = 2, post_hours: float = 6) -> dict:
    """Replay every historical release: which trades opened around each event type, and P&L after bullish/bearish surprises."""
    r = store.load_run(run_id); return _clean(E.event_replay(r["trades"], E.load_events(events), r["spec"]["symbol"], pre_hours, post_hours))


# ---------- MQL5 export / MT5 Strategy Tester ----------
@mcp.tool()
def export_mql5(strategy: str, version: int | None = None, events: str | None = None) -> dict:
    """Generate the .mq5 EA (+ events CSV for the tester) from a saved spec. Written to qp_data/mql5_out/."""
    from .models import HOME
    spec, v = store.get_strategy(strategy, version); out = HOME / "mql5_out"; out.mkdir(exist_ok=True)
    src = MQ.export_mq5(spec); p = out / f"{spec['name']}.mq5"; p.write_text(src, encoding="utf-8")
    res = {"mq5": str(p), "version": v, "lines": len(src.splitlines())}
    if events:
        csv = out / "qp_events.csv"; E.export_for_mql5(E.load_events(events), str(csv)); res["events_csv"] = str(csv)
        res["note"] = "copy qp_events.csv to <MT5 Common>\\Files (File > Open Folder > Common) before running the tester"
    return res


@mcp.tool()
def mt5_compile(strategy: str, version: int | None = None) -> dict:
    """Export + install into MQL5\\Experts\\QP + headless compile. Returns compiler errors (line/col) so you can fix and retry. Windows only."""
    spec, _ = store.get_strategy(strategy, version); p = TST.install_source(spec["name"], MQ.export_mq5(spec)); return TST.compile_ea(p)


@mcp.tool()
def mt5_run_tester(strategy: str, date_from: str, date_to: str, model: str = "ohlc_1m", deposit: float = 10000, leverage: int = 100,
                   python_run_id: str | None = None, version: int | None = None) -> dict:
    """Run the compiled EA in the real MT5 Strategy Tester (model: ohlc_1m | every_tick | real_ticks | open_prices), dates YYYY.MM.DD.
    Pass python_run_id to get a Python-vs-MT5 parity report. Windows only; use a dedicated portable terminal."""
    spec, v = store.get_strategy(strategy, version)
    res = TST.run_tester(f"QP\\{spec['name']}", spec["symbol"], spec["timeframe"], date_from, date_to, deposit=deposit, leverage=leverage, model=model)
    if res.get("ok"):
        run = {"kind": "mt5_tester", "strategy": spec["name"], "version": v, "spec": spec, "broker": "mt5_terminal", "scenario": model, "metrics": res["metrics"]}
        res["run_id"] = store.save_run(run)
        if python_run_id:
            py = store.load_run(python_run_id)["metrics"]; mt = res["metrics"]
            res["parity"] = {"python": {k: py.get(k) for k in ("trades", "net_profit", "profit_factor")}, "mt5": {k: mt.get(k) for k in ("trades", "net_profit", "profit_factor")},
                             "verdict": "Expect same ballpark, not identical (indicator seeding, tick model, real spreads). Large gaps => check broker profile, session/DST handling, events CSV."}
    return _clean(res)


# ---------- TradingView ----------
@mcp.tool()
def tv_recent_signals(n: int = 20, only_unhandled: bool = False) -> list:
    """Signals received by the TradingView webhook (run `uvicorn quantpipe.tv_webhook:app`)."""
    return store.get_signals(n, only_unhandled)


@mcp.tool()
def tv_execute_signal(signal_id: int, broker: str = "ecn_raw", risk_pct: float = 0.5) -> dict:
    """Manually route a stored TV signal through RiskGuard to MT5 (or paper). Needs sl in the signal."""
    sig = next((s for s in store.get_signals(500) if s["id"] == signal_id), None)
    if not sig: return {"ok": False, "reason": "signal not found"}
    p = sig["payload"]; strat = p.get("strategy", "tv"); magic = store.magic_for(strat)
    if sig["action"].startswith("close"): res = ex.close_all(sig["symbol"], magic)
    else:
        sl = float(p.get("sl") or 0) or None
        res = ex.place(sig["symbol"], sig["action"], sl, float(p.get("tp") or 0) or None, risk_pct, None, broker, strat, magic)
    store.mark_handled(signal_id); return {"result": res}


@mcp.tool()
def pine_alert_template(strategy_name: str, risk_pct: float = 0.5) -> str:
    """Alert message JSON to paste into a TradingView alert (webhook URL = https://<host>/tv)."""
    return ('{"secret":"<TV_WEBHOOK_SECRET>","strategy":"%s","symbol":"{{ticker}}","action":"{{strategy.order.action}}",'
            '"price":{{close}},"interval":"{{interval}}","sl":<SL_PRICE>,"tp":<TP_PRICE>,"risk_pct":%s,"time":"{{timenow}}"}') % (strategy_name, risk_pct)


# ---------- MT5 / live ----------
@mcp.tool()
def mt5_account() -> dict:
    """Account state + current mode (paper/live)."""
    c = ex.get_client(); return _clean({"mode": c.kind, "account": c.account(), "positions": c.positions()})


@mcp.tool()
def mt5_place_order(symbol: str, side: str, sl_price: float, tp_price: float | None = None, risk_pct: float = 0.5,
                    broker: str = "ecn_raw", strategy: str = "manual") -> dict:
    """Risk-sized market order through RiskGuard. side buy|sell. Paper unless QP_MODE=live + QP_LIVE_ENABLED=1."""
    return _clean(ex.place(symbol, side, sl_price, tp_price, risk_pct, None, broker, strategy, store.magic_for(strategy)))


@mcp.tool()
def mt5_close_all(symbol: str | None = None, strategy: str | None = None) -> list:
    return _clean(ex.close_all(symbol, store.magic_for(strategy) if strategy else None))


@mcp.tool()
def deploy_strategy(strategy: str, broker: str = "ecn_raw", version: int | None = None, name: str | None = None,
                    require_validation: bool = True) -> dict:
    """Activate a saved strategy for the autonomous live_runner. Requires that a passing stress_test and a holdout run
    exist for this strategy (override with require_validation=false at your own risk)."""
    spec, v = store.get_strategy(strategy, version)
    if require_validation:
        runs = [store.load_run(r["run_id"]) for r in store.list_runs(strategy, 200)]
        has_stress = any(r.get("kind") == "stress" and r["result"].get("robust") and r.get("version") == v for r in runs)
        has_hold = any(r.get("kind") == "backtest" and r.get("segment") == "holdout" and r.get("version") == v
                       and r["metrics"].get("net_profit", 0) > 0 for r in runs)
        if not (has_stress and has_hold):
            return {"deployed": False, "reason": f"validation gate: robust stress_test={has_stress}, profitable holdout backtest={has_hold} (version {v})"}
    store.set_deployment(name or strategy, strategy, v, broker, ex.get_client().kind); store.log_event("deploy", {"strategy": strategy, "v": v})
    return {"deployed": True, "name": name or strategy, "version": v, "mode": ex.get_client().kind,
            "note": "start the loop with: python -m quantpipe.live_runner"}


@mcp.tool()
def undeploy_strategy(name: str) -> str:
    with store.conn() as c: c.execute("UPDATE deployments SET active=0 WHERE name=?", (name,))
    return f"{name} deactivated"


@mcp.tool()
def set_kill_switch(on: bool) -> str:
    """Emergency stop: blocks ALL new orders from every path."""
    store.kv_set("kill_switch", on); store.log_event("kill_switch", {"on": on}, "warn"); return f"kill_switch={on}"


@mcp.tool()
def recent_events(n: int = 30, kind: str | None = None) -> list:
    """Audit log: orders, blocked orders, runner ticks/errors."""
    return store.recent_events(n, kind)


@mcp.prompt()
def improve_strategy(strategy: str, dataset: str, max_iterations: int = 6) -> str:
    """Autonomous research loop with anti-overfitting guardrails."""
    return f"""You are a systematic quant researcher improving strategy '{strategy}' on dataset '{dataset}'.
Call get_spec_schema first. Loop up to {max_iterations} iterations:
0. If using macro/fundamental ideas: event_study FIRST (does the surprise move this instrument in this data?). No reaction => no macro strategy.
   Always pass events=<set> to backtest/stress/optimize/walk_forward so technical and fundamental data stay aligned.
1. backtest (segment=train, broker=ecn_raw, baseline). Read metrics AND diagnostics.flags (incl. EVENT_LOSSES / by_event_context).
2. Form ONE hypothesis from the flags (e.g. REGIME_WEAKNESS -> add ADX filter; COST_DRAG -> higher timeframe/wider targets;
   SIDE_ASYMMETRY -> drop the losing side). Change ONE thing; save_strategy with the hypothesis in notes.
3. Re-run backtest; compare_runs vs previous. Keep the change only if it helps on sharpe AND drawdown AND trade count stays >= 30.
4. If params are involved: optimize_params with a SMALL grid (<=3 params, <=4 values each), then walk_forward_test. Reject on FAIL.
5. When a version looks good: stress_test across ALL brokers/scenarios. Need robust=true.
6. Only then run backtest segment=holdout ONCE. Never tune on holdout results.
6b. If on Windows with MT5: export_mql5 -> mt5_compile (fix errors, retry) -> mt5_run_tester with python_run_id for a parity report;
   re-test with model=every_tick/real_ticks. Large Python-vs-MT5 gaps mean the Python result is not trustworthy yet.
7. Report: version history, what each change did, final robustness, and honest caveats (synthetic data, execution differences).
Never claim future profitability. Prefer simple rules; reject complexity that adds < 10% to sharpe."""


def main(): mcp.run()


if __name__ == "__main__":
    main()
