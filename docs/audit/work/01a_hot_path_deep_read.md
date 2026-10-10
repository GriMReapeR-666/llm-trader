# 01a: Hot-path deep read (line-by-line)

**Date:** 2026-10-04 · **Commit:** `3e7baf1` + working tree · **Builds on:** `docs/audit/AUDIT_2026-10-04.md` (severity scale reused).
**Method:** read every line of the scoped code; each finding was checked against callers and, where marked **PROBED**, reproduced with a small offline Python probe (`/private/tmp/audit01a/probe*.py`, FAST_TEST=1, no network, no MT5, no tracked files changed).

Scope read in full: `core/trade_manager.py` (all), `intelligence/llm_orchestrator.py` 7336-8620 (execute_cycle up to and just past `get_decision`), `_apply_advanced_auto_tools` (4581-4970), `_process_due_schedules` (5910-6540) plus `_run_schedules_bounded`, `reconcile_open_positions` (5111-5305, pulled in by a finding), `data/market_data_provider.py`, `data/data_quality.py`, `data/data_requirements.py`. Additional reads where a finding needed it: `core/risk_manager.py` 680-900, `core/order_execution.py` 1-200, `core/harness/plugins.py` 75-130, `research/rl_action_bridge.py`, `core/execution_safety_supervisor.py` 110-150.

---

## Findings

### F1 🔴 The always-on PaperBroker hides every LIVE position from position management, the position limit and portfolio heat

`intelligence/llm_orchestrator.py:809-817` runs in **every** mode (there is no `paper_mode` check in `__init__`):

```python
self.paper_broker = PaperBroker()
...trade_manager.attach_paper_broker(self.paper_broker)
self.risk.set_active_positions_provider(lambda: len(getattr(self.paper_broker, "positions", {})))
self.risk.set_portfolio_heat_provider(self._calc_paper_portfolio_heat)
```

and `wire_trade_manager` (`:839-841`), which `mt5_trader.py:4117` calls for the harness TradeManager in live runs, attaches it again unconditionally. Nothing in the repo ever sets `paper_broker` back to `None` (grep: the only assignments are `:811` and `:819`, the `NameError` fallback).

Consequences in a `--live` run:

| Where | Code | Effect |
|---|---|---|
| `core/trade_manager.py:124-132` | `if self.paper_broker is not None: … return res` | `_get_positions()` returns the (empty) paper book. **Breakeven, trailing, scale-out, time stop and pyramiding never run on live positions.** |
| `core/trade_manager.py:235-239` | `if not all_positions: … clear_flatten_request(); return True` | **The daily-loss protective flatten (GAP-8) clears the flatten request without closing a single live position.** |
| `core/risk_manager.py:703-707` | `count = self._active_positions_provider()` → `return count < self.max_active_positions` | `check_active_trade_limit()` counts paper positions (0) and returns before the MT5 branch. **Max simultaneous positions is never enforced live** (called at `llm_orchestrator.py:8923`). |
| `core/risk_manager.py:777-781` | `custom_heat = self._portfolio_heat_provider()` | `open_portfolio_heat_r()` returns 0.0 from the paper book, so the heat cap (`:889-894`, `:832`) always passes live. |
| `core/trade_manager.py:1000-1015`, `:505-530`, `:1190-1220` | paper branches | SL modifies / closes requested on live positions would go to the paper broker (moot today because F1 row 1 means no live position is ever seen). |

**PROBED** (`probe1.py`): a TradeManager with one live MT5 position at +3R and a PaperBroker attached issues **0** `order_send` calls and sees `[]` positions; `RiskManager.check_active_trade_limit()` with 10 live owned positions and `max_active_positions=3` returns **False** without the provider and **True** with the paper provider.

**Scenario:** live account, daily loss limit hit at 14:00. `risk.pending_flatten_reason()` is set, `enforce_protective_flatten()` sees no positions, clears the request, and returns. The open losers keep running with no breakeven or trail. Meanwhile the engine keeps opening new positions, because the position-count and heat caps read 0.

**Fix:** construct `PaperBroker` and install the paper providers only when the journal mode is PAPER (pass `paper_mode` into `__init__`, or make `wire_trade_manager`/mt5_trader detach it for LIVE/DEMO). Add a boot assertion: in LIVE, `trade_manager.paper_broker is None` and `risk._active_positions_provider is None`. Add a test that builds the orchestrator in live journal mode and asserts both.

### F2 🔴 Live reconciliation records every open live trade as CLOSED at 0 P&L (and the "do not fabricate" guard is unreachable)

Same root as F1. `reconcile_open_positions` (`llm_orchestrator.py:5119-5121`) branches on the paper broker, not on mode:

```python
if self.paper_broker:
    actual_tickets = set(self.paper_broker.positions.keys())   # always empty in live
```

It is called **only when `not paper_mode`** (`:7063-7064`, `:7206-7207`, `:6317-6318`, `core/harness/pipeline_v2.py:302-304`: "legacy only runs it live"). So in live every durable open trade is "absent", the paper-history lookup is empty, and `:5149-5155` fabricate `gross_pnl = 0.0`, `exit_price = open_price`, `close_time = now`. Then `:5182-5184`:

```python
self.store.record_close(ticket, exit_price, total_pnl, "History reconciliation", …)
if hasattr(self.risk, "record_trade_outcome"):
    self.risk.record_trade_outcome(total_pnl)
```

The comment at `:5300-5304` ("Do not fabricate a zero-PnL outcome. Keep it open and retry") sits on the `else:` of **`if hasattr(self.risk, "record_trade_outcome")`** (`:5185` / `:5300`), not on the "no history" branch. It is effectively dead code: the risk manager always has that method.

Two more defects on the same path:
- `:5190` `… or [deals[-1]]` raises `IndexError` when `deals == []`, **after** `record_close` has already been committed. One fabricated close per reconcile call, and the rest of the loop aborts.
- The fabricated outcome is fed to `RiskManager.record_trade_outcome(0.0)`. A zero does not reset the loss breaker (`core/risk_manager.py:354-361` treats breakeven as neutral), but the real loss or win of that trade is never recorded, because the ticket is already closed in the journal when the broker actually closes it.

**PROBED** (`probe2.py`): with a PaperBroker attached and one open journal row (ticket 777), `reconcile_open_positions` calls `store.record_close(777, 1.1, 0.0, 'History reconciliation', …)` and `risk.record_trade_outcome(0.0)`, then raises `IndexError`.

**Scenario:** a live trade opens at 10:00. The next maintenance pulse marks it CLOSED/0 P&L in `trades`, the setup lifecycle goes to CLOSED, RL/memory/lesson distillation learn a fake breakeven, and `store.trade_risk()` / `open_trade_rows()` stop counting it. Duplicate-intent and arbiter checks then see free risk budget, while the real position stays open and unmanaged (F1).

**Fix:** branch on journal mode, not on `self.paper_broker`. Move the no-history `else` onto the `if deals:` / MT5-history branch so a missing history really leaves the trade open. Guard `deals[-1]`. 

### F3 🟠 `risk_per_trade` is mutated multiplicatively on every cycle, with no reset; the crash detector does it live with no approval gate

`_apply_advanced_auto_tools` (`llm_orchestrator.py:4759-4767`):

```python
base_risk = float(getattr(self.risk, "risk_per_trade", 0.01))
crash_risk = max(0.001, min(0.02, base_risk * risk_mult))
self.risk.risk_per_trade = crash_risk
```

- `base_risk` is the **current, already-mutated** value, so the multiplier compounds every cycle. `crash_detector` runs every cycle (`core/auto_quant_tools.py:59`). Multipliers are 0.3 / 0.6 / **1.2** / 1.0 / 0.7 (`core/crash_detector.py:169-173`).
- Unlike meta-adaptive (`:4613`) and self-improvement (`:4644`), this mutation has **no `_research_influence_allowed` gate**, so it applies in LIVE.
- Nothing ever restores the configured value: the only writers of `risk.risk_per_trade` are `:4615`, `:4646` and `:4763`.
- `self.risk` is one object shared by every strategy thread (`mt5_trader.py:4755-4760`), so one symbol's regime rescales every other symbol's sizing. The meta-adaptive path (`:4611-4615`) compounds the same way in paper, and in live once research influence is approved.

**PROBED** (`probe3.py`, live, research approval False): with `RAPID_ASCEND` (×1.2), `risk_per_trade` goes 0.012 → 0.0144 → 0.0173 → **0.02** by cycle 4, double the configured 1 %. With `OSCILLATION` (×0.7) it goes 0.007 → … → 0.001 by cycle 7, and sizing collapses to a tenth.

**Fix:** store the operator's base risk once (`self._base_risk_per_trade`) and compute `effective = base × Π(multipliers of this cycle)` without writing it back. Pass the effective risk per proposal instead of mutating the shared RiskManager. Gate the crash-detector *increase* (mult > 1) behind the same research-influence approval; a decrease can stay ungated.

### F4 🟠 RL epsilon-exploration can turn an LLM HOLD into BUY/SELL; the "live" guard is a dead check

`llm_orchestrator.py:8389-8391`:

```python
has_paper_broker = getattr(self.data_provider, "paper", None) is not None
has_execution_authority = getattr(self.data_provider, "execution_authority", False)
non_live = has_paper_broker or not has_execution_authority
```

Neither attribute exists on `MarketDataProvider` (grep: no assignment anywhere), so `non_live` is **always True**. RL action selection (`select_action(..., explore=True)`, `:8400`) then runs whenever `paper_mode` is set, `TRADING_RL_ENABLE_UPDATES=true`, or research approval for `"rl"` exists. After `get_decision`, `:8570-8575` call `merge_bridge_into_decision(decision, bridged, fill_missing_only=True)`. In `research/rl_action_bridge.py:230-232`:

```python
if current_action in {"", "HOLD", "NONE", "NULL"}:
    out["action"] = bridged.action
```

So an LLM **HOLD** becomes the RL's BUY/SELL, with ATR-derived entry/SL/TP and the LLM's own confidence. The comment at `:8541` calls this "fill missing continuous params … (advisory only)", and the docstring at `:8383-8384` says "A live broker run must remain deterministic and model-independent".

**PROBED** (`probe3.py`): `merge_bridge_into_decision({"action":"HOLD","confidence":0.7}, BUY…)` returns `action: 'BUY'` with entry/SL/TP filled.

**Scenario:** an operator sets `TRADING_RL_ENABLE_UPDATES=true` in live because they want the RL to *learn* (the name says updates). A random exploration action now originates live trades on candles the LLM chose to skip. The deterministic validator still runs, but setup classification is feature-based, so an eligible setup passes. In paper this happens on every exploration step, so the paper track record that feeds promotion is partly random RL entries attributed to the LLM.

**Fix:** never let the bridge change `action`; fill only numeric fields when the action is already BUY/SELL. Replace the `non_live` heuristic with the real journal mode.

### F5 🟠 The market-order spread-explosion guard can never fire

`data/market_data_provider.py:1002-1008`:

```python
_current_spread = (ask - bid) / max(bid, 1e-10) * 10000
... current_spread_pips=_current_spread,
    historical_avg_spread_pips=max(_current_spread * 0.8, 0.5),
```

The "historical average" is derived from the current spread itself. `core/execution_safety_supervisor.py:139-140` computes `spread_ratio = current / max(0.1, hist)`, which is at most 1.25 (or `cur/0.5` while `cur < 0.625`), against `max_slippage_multiplier = 2.5` (`:29`). A spread of 0.625 bps or more can never be rejected, however wide it gets. The unit is also basis points of price, not pips. And if `_symbol_tick` throws (`:991-996`), spread and latency stay 0.0 and the check **passes** on missing evidence.

The prior audit (§4 workflow flaws) assumes a spread veto exists; this one is inert on the `place_urgency_routed_order` / market path. **Fix:** keep a rolling per-symbol spread EMA (`MicrostructureObserver` already sees every DOM), pass that as the baseline, and reject when the tick is unavailable.

### F6 🟠 During an LLM outage, an institutional-workflow proposal is promoted to a trade at hard-coded confidence 0.85

`llm_orchestrator.py:8547-8564`: when `decision.get("deterministic_fallback")` is set (the provider is down), and `toon["institutional_workflow"]["decision"] == "EXECUTE"`, the decision is replaced by the workflow's side/entry/SL/TP with `"confidence": 0.85` and `provider_outage = False`. The comments two lines above say "The safety HOLD must stay absolute". This contradicts the prior audit's "what works well" claim ("Deterministic fallbacks can never escalate to a trade", AUDIT §4, citing `:1971-1975`). That claim holds inside `get_decision`, but this code path re-escalates after it returns. The validator still runs.

**Fix:** remove the promotion, or put it behind an explicit operator flag that is off in LIVE, and never hard-code confidence.

### F7 🟡 `reconcile_open_positions` runs concurrently in several threads without a lock (double close accounting)

Every strategy runs `execute_cycle` on its own pool thread (`mt5_trader.py:4755-4760`). Each calls `_run_maintenance_prefix` → `reconcile_open_positions()` (`:7063-7064`), and the background `sched-bounded` worker can run `position_reconcile` at the same time (`:6314-6318`, `_run_schedules_bounded:5636`). The method snapshots `open_trade_rows()` and then acts on it, with no lock and no compare-and-set on `closed_at IS NULL`. Two threads seeing the same closed ticket each call `record_close`, `risk.record_trade_outcome(pnl)` (double-counted loss streak and weekly step-down), `label_closed_trade`, `rebuild_scenarios` and lesson distillation, and each submits a reflection job. **Fix:** a per-ticket lock, or `UPDATE trades … WHERE ticket=? AND closed_at IS NULL` with side effects only when `rowcount == 1`.

### F8 🔴 In PAPER mode, pyramiding sends a REAL `mt5.order_send` to whatever account the terminal is logged into

`core/trade_manager.py:739-820` (`_execute_pyramid_order`) has no paper branch, unlike `_execute_partial_close` (`:505`), `_modify_sl` (`:1000`) and `_close_position` (`:1190`). For a paper position (a `PaperPosition` from the paper book) it calls `mt5.symbol_info_tick`, `_broker_filling_mode` (real `mt5.symbol_info`) and then:

```python
res = mt5.order_send(request)   # :798  TRADE_ACTION_DEAL, type=pos.type, no "position" → opens a NEW position
```

The PYRAMID authorization (`:300-364`) uses the real `mt5.account_info().equity`. Paper mode is attached to a real terminal by design: market data fails closed without one (`fetch_ohlc`, `market_data_provider.py:225-278`), and `_ensure_mt5_attached` auto-initializes it. `monitor_positions` is called with indicators every cycle (`llm_orchestrator.py:9348`), so the pyramid gate (ADX ≥ 25 and profit > 1.5R) is reachable in normal paper operation.

**PROBED** (`probe4.py`): a paper BUY at +3R with ADX 30 and the arbiter permitting produced `order_send({'symbol':'EURUSD','volume':0.1,'type':0,'price':1.1031,'sl':1.099,'tp':1.11,'magic':460001,'comment':'pyramid_entry',…})` on the MT5 module. The paper book was not touched.

The real order skips the DeterministicValidator, the duplicate-intent guard, margin preflight, the Ψ guardian, the execution-safety supervisor, the kill-switch *latch* (only `store.is_kill_switch_active()` is checked) and the journal's order-intent path. It also uses the **stale** `pos.sl` (`:761`): in the probe, the paper SL had already been trailed to 1.1015 in the same tick, but the real order went out with SL 1.0990.

**Scenario:** an operator "paper trades" with the terminal logged into their live account (the runbook requires a terminal for paper). The first paper winner that trends opens a real 0.1-lot live position that the engine then ignores. The paper `_get_positions` never sees it, and reconciliation is skipped in paper.

**Fix:** add `if self.paper_broker is not None: return self.paper_broker.market_order(...)` (or refuse) at the top of the order path. Longer term, route pyramids through the same order gateway as entries (validator → sizing → journal → send), as AUDIT §2.3 recommends for UI orders. Treat this as another instance of C3's "one order gateway" fix.

### F9 🟡 Pyramid and time-stop R basis use the *current* SL, so pyramids fire at 1.5 ATR, not 1.5R

`_check_pyramid_entry` (`:653`) and `_check_time_stop` (`:464`) compute `risk_dist = abs(pos.price_open - pos.sl)` from the live SL, while the docstring and the rest of the ladder use the cached initial risk (`_initial_risk_for`, `:402-418`). After breakeven (`+1R` → SL = open + 0.1·ATR), `risk_dist` becomes 0.1·ATR, so `profit > 1.5 * risk_dist` is trivially true, and the add triggers at `profit > 1.5·ATR` instead of 1.5R. For the time stop after breakeven, `r_mult` is inflated about 10×, so a stagnant trade sitting at +0.05·ATR scores 0.5R and is never time-stopped. The pyramid's risk estimate (`:338-341`, `equity·risk_fraction·vol/parent_vol`) assumes the add risks half a parent-R. With SL left at the parent's stop and entry about 1.5R higher, it actually risks about 0.75R or more, so the arbiter is given an under-estimate. Each pyramid child is a new owned ticket that is not in `pyramid_entries`, so a child can pyramid again; growth is bounded only by the (broken, F1) position limit and the arbiter. **Fix:** use `_initial_risk_for` everywhere, compute the add's risk from `(entry - sl)·vol·tick_value`, and tag children so they cannot pyramid.

### F10 🟡 The time stop and position duration compare MT5 server-time epochs with UTC `time.time()`

`trade_manager.py:455-459`: `elapsed = time.time() - pos.time`. MT5 `position.time` is in broker-server time, the same quirk `market_data_provider._server_utc_offset_seconds` (`:131-184`) corrects for bars. On a UTC+3 server, `elapsed` is 3h short, so the 4h time stop fires after about 7h. `get_position_context` (`:852`), which feeds the LLM prompt (`llm_orchestrator.py:8295`), shows a duration that is 180 minutes short as well. The same applies to `reconcile_open_positions` close times (`llm_orchestrator.py:5170-5175`: `datetime.fromtimestamp(time_msc/1000, tz=utc)`), which stores `closed_at` up to 3h in the future and can attribute a 22:00-UTC close to the next day. **Fix:** subtract `_server_utc_offset_seconds(mt5)` for every MT5 epoch (positions, deals, orders) in one helper.

### F11 🟡 Paper SL updates hit every position with the same symbol and magic, and skip the monotonic guard

`_modify_sl` paper branch (`trade_manager.py:1000-1015`) calls `paper_broker.modify_position_sltp(symbol=pos.symbol, sl=…, magic=…)`, which (`core/order_execution.py:92-104`) updates **all** positions matching symbol+magic, not the ticket. A breakeven on ticket A rewrites the SL of ticket B, including an opposite-side B, where the SL may land on the wrong side of price. The paper branch also has no "only tighten" check (the live branch has one at `:1036-1040`). Breakeven and trailing are evaluated against the same stale `current_sl` in one tick (`:592-624`), so when `0.1·ATR > 1.5R - …` the trail *lowers* the stop that breakeven just raised. Example: R = 0.001, ATR = 0.01, price = open + 1.6R. BE sets open + 0.001, then the trail sets open + 0.0001, and the two alternate every tick. Paper results (which feed promotion) are therefore not representative of live. **Fix:** a ticket-scoped `modify_position_sltp(ticket=…)` and the same monotonic/protective-side checks as live; compute the target SL once as `max(be, trail)`.

### F12 ⚪ Smaller trade_manager defects

- `execute_partial_close` (LLM wrapper, `:923-932`) omits `type_filling`, so IOC-only brokers reject it. It also marks the ticket in `partial_exits` (`:937`), which silently skips the automatic stage-1 scale-out. It uses `mt5.positions_get` directly (`:890`), so it never works in paper.
- `_close_position` (`:1230-1242`) sends without `type_filling` when the mode is unknown, and falls back to `pos.price_current` with no tick. `enforce_protective_flatten` sets `closed_any = True` even when the close failed (`:256-257`).
- `monitor_positions` has no per-position `try`: an exception in `_manage_position` for one ticket (e.g. `mt5` is `None` on a host without the package, so `mt5.ORDER_TYPE_BUY` raises at `:592`) aborts management of every later position for that symbol.
- `UNOWNED_POSITION_OBSERVED` is journalled on every tick for every manual position (`:180-187`), so the journal grows without bound. `self.history` (`:103`) is never trimmed.
- `_initial_risk_for` (`:410-417`) caches `abs(open - sl)` from whatever SL is present the *first* time a ticket is seen. If the state was lost (the `[-500:]` truncation at `:395`, or the store was not attached), it captures a breakeven or trailed SL as "R", which shrinks R and triggers scale-outs early.

### F13 🟠 In LIVE/DEMO the pre-analysis data-quality gate rejects every cycle; past it, the validator rejects every proposal on a non-UTC broker

`execute_cycle` (`llm_orchestrator.py:7483-7485`) runs `self.data_quality.validate(toon, require_live_quote=(not paper_mode))` on the raw `fetch_data` output. With `require_live_quote=True`, `DataQualityGate.validate` requires `context["clock_health"]` (`data/data_quality.py:98-103`) and `context["provider_time"]` (`:113-115`). `fetch_data` never sets them (`market_data_provider.py:1884-1894`). The **only** writer is `_validate_proposal` (`llm_orchestrator.py:2844-2851`), which runs after the LLM. So every non-paper cycle returns HOLD at `:7500` with `"broker/local clock-health observation missing; provider-time observation is missing"`. The cycle also *completes* the task (`:7497`), so the candle is consumed (the news gate correctly *releases* it, `:7562`).

Even with that fixed, `_validate_proposal` builds the clock evidence from `tick.time_msc`, which is broker-server time, and treats it as UTC (`:2842-2851`, `operations/agent_operations.py:776-778`). On a UTC+2/UTC+3 server, the offset is 7,200 to 10,800 s against a 5 s limit, so `clock_health.valid=False` and `provider_time` sits 2-3 h from `captured_at`: two more rejections. `fetch_ohlc` already knows the offset (`_server_utc_offset_seconds`); this path doesn't use it.

**PROBED** (`probe5.py`): a toon with exactly `fetch_data`'s context keys gives `['broker/local clock-health observation missing', 'provider-time observation is missing']`. With UTC+3 tick evidence, it gives `['broker tick is stale or local/broker clock skew exceeds threshold', 'provider/local snapshot time differs beyond threshold']`.

**Impact:** this is fail-closed, so no money is lost, but the live/demo decision path cannot produce a trade as written. The tests pass because `tests/test_data_quality.py` hand-builds both keys. **Important interaction:** whoever fixes this ordering immediately exposes F1, F2, F3, F5 and F8's live variant. Fix those first, or together. **Fix:** collect the tick and clock observation inside `fetch_data` (subtracting the server offset), and stamp `provider_time` from the corrected tick.

### F14 🟡 `captured_at` is stamped after the slow work, so the 5-second freshness check measures nothing

`market_data_provider.py:1887-1888` sets `now_utc`/`captured_at` when the toon dict is built at the **end** of `fetch_data`. By then, OHLC (`:1325`) and DOM/bid/ask (`:1342-1351`) were read before the HTF fetch, the alpha ensemble, auto-quant tools, NewsAPI, FRED (network) and six more `fetch_ohlc` calls for `multi_timeframe` (`:1858`). The DQ gate's `max_snapshot_age_seconds=5` (`data_quality.py:38`, `:91-93`) compares against this late stamp, so quotes that are tens of seconds old pass as fresh. The DOM snapshot has its own `captured_at`, but it is only checked when `require_dom` is set (one setup). **Fix:** stamp `captured_at` (and `provider_time`) at the quote read, and carry the DOM's `captured_at` into the live-quote freshness check.

### F15 🟡 Pre-trade gates that fail **open** on exception in the hot path

| Location | Code | Note |
|---|---|---|
| `llm_orchestrator.py:7502-7503` | `except Exception as _dq_exc: log.debug("DataQualityGate check skipped (fail-open)…")` | The gate instance is shared by every strategy thread. `validate` mutates `_candle_fingerprints` and prunes it with `list(self._candle_fingerprints)[:2000]` (`data_quality.py:228-230`) with no lock, so a concurrent insert can raise `RuntimeError: dictionary changed size during iteration`, and the cycle then proceeds without the gate. (The validator re-runs DQ at `:2852`, so this is defence in depth.) |
| `llm_orchestrator.py:7629-7630` | `except Exception: log.debug("NewsTradingGate check skipped (fail-open)")` | Any exception in calendar fetch, `NewsTradingGate.evaluate` or `get_news_trading_context` skips the blackout gate and leaves no `toon["news_gate"]`. `EventPolicy` (`:7826`) is a second gate, so partial defence remains. Debug-level logging hides it. |
| `market_data_provider.py:1063-1064` | `log.warning("Ψ Guardian check failed (fail-open)")` on `place_pending_order` | The market-order wrapper fails **closed** on the same exception (`:968-976`), and pending orders are the main entry route (`llm_orchestrator.py:9200-9212`). An exception inside the guardian check therefore disables the "cannot be countermanded" kill authority for pending orders only. |
| `llm_orchestrator.py:7636` | `if _corr_engine is not None:` | `_corr_engine` is bound inside the news `try` after `from core.news_trading import …`. If that import fails, this line raises `NameError` and aborts the cycle with the candle task still claimed (fail-closed, but it leaks the lease until expiry). |

**Fix:** make every gate exception return HOLD (and release the task), and log at WARNING or above.

### F16 🟡 Copula heat budget latches at the crisis value and never returns to normal

`market_data_provider.py:1570-1572`: `if … abs(adj - 4.0) > 0.01: self.risk.set_heat_budget_r(adj)`. When the copula returns to the normal budget (4.0), nothing is written, so a single crisis reading (`crisis_heat_budget_r=1.0`) leaves `max_portfolio_heat_r = 1.0` for the rest of the process. With `heat + 1.0 > 1.0` (`risk_manager.py:832`, `:889-890`), any open position then blocks every new entry, which is live in paper today and in live once F1 is fixed. It is also shared across symbols: one symbol's tail regime sets the budget for all. **Fix:** always write `adj` (including 4.0), or recompute the budget per proposal from a stored base.

### F17 ⚪ Smaller findings in the scoped code

- `market_data_provider.py:1372` reads `volume_data` before it is assigned (`:1395`), so the `NameError` is swallowed at debug and the ADV tracker never updates from bar volume.
- `market_data_provider.py:1541`: `CorrelationRisk(db_path="trading_state.db")` is hard-coded and ignores the configured store path (`self._state_store()`), so a non-default journal path splits correlation state.
- `market_data_provider.py:1159-1167`, `:1332`: multi-timeframe and HTF `trend` come from the **forming** bar (`iloc[-1]`). That trend feeds `features["mtf_alignment"]` in `_validate_proposal` (`llm_orchestrator.py:2368-2370`), whose own comment says "never the forming bar", so the setup classification is not point-in-time reproducible.
- `llm_orchestrator.py:4846-4847`: `data_drift_hold_bias` is set with "HOLD bias injected", but no gate reads it (grep: only the summary at `:4949`), unlike the crash, walk-forward and execution-cost biases, which `_validate_proposal` enforces (`:2539-2626`).
- `llm_orchestrator.py:7644-7660`: event-time parsing takes the first present key even if it fails to parse, and a naive ISO string produces a `TypeError` on `_now_utc - _ev_time` that aborts outcome recording for every remaining event (advisory only).
- `llm_orchestrator.py:7606`: the news execution plan defaults `account_balance` to 100,000 when the store has none (advisory, but it is prompt context).
- `llm_orchestrator.py:8326`: `profit_factor = … else 99.9` with no losing trades is shown to the LLM as a real metric.
- `PerformanceMonitor.load_history` (`market_data_provider.py:506-513`) reads `price_open` and `float(opened_at)` from journal rows that use `open_price` and ISO strings. The `ValueError` is swallowed, so restored history is empty or zero-priced. `close_trade` (`:567-568`) invents `risk_amount = volume*100` when missing, which fabricates R-multiples for RL and the meta-loop.
- `_persist_alert_metrics` (`:680-686`) counts `pnl <= 0` as a loss, while `RiskManager.record_trade_outcome` treats `pnl == 0` as neutral (`risk_manager.py:354-361`). The two `consecutive_losses` figures (the store state used by alerts, and the breaker) therefore disagree after breakeven exits and after F2's fabricated zero closes.

### F18 🟡 A second, undocumented management engine runs on the same positions, and its partial-close branch is dead

The tick path (`llm_orchestrator.py:4004-4058`) calls `trade_manager.monitor_positions(...)` and then **also** runs `core/position_manager.PositionManager` over the same positions. It registers each ticket with its *current* SL and executes its actions through TradeManager:

- `PARTIAL_CLOSE` calls `self.data_provider.trade_manager._find_pos(_act.ticket)`. **`_find_pos` does not exist** (grep: this call is the only occurrence). The `AttributeError` is swallowed at debug (`:4049-4050`), so PM partial closes never execute, while `pos.partial_exits_done` still increments.
- PM time stop and regime close are dead too: `manage()` is called without `bar_number` or `regime_changed` (`:4030-4032`), so `bars_held` is always 0 (`position_manager.py:283`), and the OU branch needs at least 20 prices built from SL moves (`:298-303`).
- PM trailing (`trailing_atr_mult=1.5`, from `+0.5R`, `position_manager.py:37-38`) is live through `_modify_sl`. Live `_modify_sl` only tightens, so the effective trail is the **tighter** of PM's `1.5·ATR` and TradeManager's `1.5R`. With a 1.5·ATR initial stop that means 1.5·ATR, not the documented "+1R breakeven, trail at 1.5R" ladder (`trade_manager.py:79-86`).
- PM state is in-memory only and never unregistered (`unregister_position` has no callers). After a restart it re-registers positions with their breakeven/trailed SL as the "initial" risk (`position_manager.py:132`), which inflates R.

**Fix:** pick one engine. If PM stays, give it the cached initial risk, a real bar counter, and a working partial-close call (or remove those rules).

### F19 🟡 The send-time margin "TOCTOU re-check" on pending orders can never run

`core/order_execution.py:768-778`:

```python
margin_check = gate.assess_from_mt5(symbol, volume=numeric_volume, price=quote_price, side=side)  # type: ignore
if not margin_check.get("allowed", True): …
except Exception as exc:
    log.warning("Margin TOCTOU re-check skipped (non-fatal): %s", exc)
```

`assess_from_mt5` is keyword-only, and `mt5_module`, `new_risk` and `driver_buckets` are required (`core/margin_safety.py:239-251`). It also returns a `MarginRiskAssessment` dataclass, which has no `.get`. **PROBED:** the call raises `TypeError … takes 1 positional argument but 2 positional arguments … were given`, which the handler logs as "skipped (non-fatal)". The `# type: ignore` hides it from type checking. The decision-time margin preflight (`llm_orchestrator.py:9070-9081`) still runs, so this is a dead second layer, not an open path. **Fix:** call it correctly (`mt5_module=mt5_inst, new_risk=…, driver_buckets=…`), test `.approved`, and fail closed on exception.

---

## Verified clean (read line by line, no defect found)

- **Live `_modify_sl`** (`trade_manager.py:1017-1040`): protective-side check against bid (BUY) / ask (SELL), broker `trade_stops_level`/`trade_freeze_level` distance, and only-tighten monotonicity on a fresh `positions_get`. BUY and SELL are symmetric, and SELL handles `current_sl == 0`.
- **Close and partial-close pricing**: BUY closes at bid with a SELL deal, SELL at ask with a BUY deal, using the `position` field (`:545-555`, `:1223-1240`). The pyramid uses ask/bid correctly (`:789`).
- **Volume normalization** (`_normalize_volume`, `:669-714`): floors to `volume_step`, clamps to min/max and the position volume, uses step-derived precision, and returns 0 (refuse) on a missing contract in live.
- **Filling mode** (`_broker_filling_mode`, `:716-737`): maps the SYMBOL_FILLING bitmask to ORDER_FILLING constants correctly and refuses to guess.
- **Ownership**: every management order path checks `_owns` (magic allowlist). LLM conditional scenarios are advisory only and never close (`:1097-1167`).
- **Authorization**: risk-reducing closes are allowed under kill switch or daily loss (correct by design); PYRAMID checks the kill switch, consecutive-loss breaker, daily drawdown, position limit (but see F1) and arbiter (`:300-364`).
- **Validator-side HOLD biases**: crash detector, walk-forward DEAD/WFE, execution cost and cross-market are actually enforced in `_validate_proposal` (`:2539-2626`, and `trading_constraints.py:762,783`). Only `data_drift_hold_bias` is not (F17).
- **LLM permit path** (`:8445-8463`): provider circuit open → HOLD; `no_llm` ablation → HOLD before budget reservation; budget refusal → HOLD; each completes the task.
- **News gate blackout** releases (does not complete) the candle task (`:7562`); the holiday gate does the same (`:7457`).
- **`fetch_ohlc`** (`market_data_provider.py:225-289`): no synthetic fallback; converts server time to UTC with a voted, residual-checked offset (`:131-184`). `ohlc_records` preserves timestamps.
- **`DataQualityGate` internals**: OHLC consistency, fingerprints only settled candles, grid-aligned gap tolerance, return z-score outlier, live bid/ask/spread sanity. The defects are in *how it is fed* (F13, F14) and *shared without a lock* (F15), not in the checks.
- **`data_requirements.evaluate`**: fail-closed on every missing input.
- **`MarginSafetyGate.assess`**: fail-closed on missing equity, margin, free margin, new margin or stop-out mode; uses gross (not hedge-netted) estimates.
- **Market-order wrapper Ψ-guardian and execution-safety exceptions** fail closed (`market_data_provider.py:968-976`, `:1016-1024`). The spread *value* is the problem (F5).
- **Schedule dispatcher** (`_run_schedules_bounded`, `_process_due_schedules`): single-flight flag is always released in `finally`, wave deadline defers and releases undispatched claims, and cadence advances at dispatch (documented exactly-once). Its only money-relevant handler, `position_reconcile`, inherits F2 and F7.

## Not verified / out of scope

- Whether any operator has run `--live` at this commit. F13 suggests live cycles cannot reach `get_decision`, which would have kept F1, F2 and F3 from causing losses so far. Positions opened by other paths (manual, older builds) are still subject to F1 and F2.
- `get_decision` and the rest of `_validate_proposal` beyond the lines cited; `place_urgency_routed_order` internals.

## Probes

`/private/tmp/audit01a/probe1.py` (F1), `probe2.py` (F2), `probe3.py` (F3, F4), `probe4.py` (F8), `probe5.py` (F13). The F19 probe is an inline call. All run offline with `.venv/bin/python` from the repo root.

## Severity count

🔴 3 (F1, F2, F8) · 🟠 5 (F3, F4, F5, F6, F13) · 🟡 9 (F7, F9, F10, F11, F14, F15, F16, F18, F19) · ⚪ 2 (F12, F17; both are grouped lists)
