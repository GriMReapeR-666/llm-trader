# Phase 2 review: one door to the broker

**Date:** 2026-10-04
**Scope:** branch `phase2/order-gateway`, which includes:
- commits `5b067a7..3bcaae5` (PR 2.1 to 2.5, plus the ROADMAP tick-off and a mock-proxy fix);
- the uncommitted working-tree changes left by the implementing agent: 16 files, +870/−378 lines, last touched 20:19.

**Method:** I read `core/order_gateway.py` in full, along with every call-site diff. I wrote failing probe tests, each asserting the correct behaviour, and ran them against the working tree. The probes live in the session scratchpad (`probes/test_gateway_probes.py`, `probes/test_phantom.py`). The focused suite (`tests/safety`, `tests/property`, auth, backtest isolation, trade-manager journal, skill registry and lint) is green on the working tree.

## Verdict

The structure is right:
- one module calls `order_send`, and the AST test enforces it;
- a runtime guard sits on `MetaTrader5.order_send`;
- deck order tools are gone;
- the exec tool is out of the tool registry;
- the backtest engine refuses the live journal;
- the API needs a token, checks Host, and drops `?token=`.

**But the gateway's risk ledger is not correct yet, and Phase 2 should not be marked done.** Two of the findings below (R1, R2) would make the bot stop opening trades after a few round trips, or size the cap wrongly, on any real account. ROADMAP.md currently ticks every Phase 2 item, so those ticks should be reverted until the items below are fixed.

## Findings (all reproduced unless marked "by reading")

### Blockers: risk ledger
| ID | Finding | Evidence | Fix |
|---|---|---|---|
| **R1** | **Phantom reservations.** FILLED and ERROR intents count as open risk forever. The dedupe only skips tickets whose trade is *still open*, so once the broker closes a trade by SL or TP, its intent's risk is counted again. The intent state machine (`agent_operations.ORDER_INTENT_TRANSITIONS`) has no CLOSED state, and FILLED is terminal. With a 3 % cap and 1 % per trade, **entries stop after about 3 closed trades**, in both paper and live, because `TradingStore.open_risk()` (used by `arbiter.permit`) has the same bug. | `probes/test_phantom.py`: after `record_close`, `open_risk()` = 100, expected 0. Gateway probe P5. Committed in `07ab8dd` (`trading_safety.py:837`). | A FILLED intent reserves nothing. Its risk lives in the `trades` row once one exists. Only CREATED, PENDING, PARTIAL and UNRESOLVED reserve. ERROR intents with no ticket must not reserve; ERROR with a ticket should become UNRESOLVED and be reconciled. |
| **R2** | **Cap computed on a fake $10,000 equity.** `submit_entry` reads `store.get_state("equity")`, which no production code ever writes, then falls back to 10 000. On the $100k demo a 1 % trade ($1,000) is always refused (cap $300). On a $500 account the cap is $300, i.e. 60 % of equity (fail open). | Probes P1, P1b. `grep set_state("equity"` finds only a test. Committed (`order_gateway.py:369`). | `OrderIntent` should carry the equity snapshot the validator and arbiter used, or the gateway should read `account_info()` under the lock. With no equity, refuse. |
| **R3** | **Double-counted and overwritten intent.** The orchestrator creates the intent (`create_order_intent`, CREATED, carrying `risk_amount`) and then passes the *same* `intent_key` to the gateway. The gateway sums active intents, which includes this one, then adds `intent.risk_amount` again. It then `INSERT OR REPLACE`s the row, which wipes `run_id`, `proposal_id`, the rich payload and `created_at`. | Probes P2 (400 > 300 for a 200 trade) and P2b (`run_id` becomes None). Committed. | The gateway should own intent creation (the plan's step 3), with the orchestrator's `create_order_intent` removed, or else exclude its own `intent_key` from the sum and `UPDATE` instead of replace. Make re-submitting an existing non-terminal key idempotent. |
| **R4** | **Failed cancel releases the reservation.** `cancel_pending` marks the intent CANCELLED even when the broker refused the cancel and the order is still working. | Probe P4. | Release only on an accepted cancel. Otherwise mark it UNRESOLVED. |
| **R5** | **The gateway bypasses the intent state machine.** `_resolve_intent`, `_close_intent_by_ticket` (`'CLOSED'`, a state that doesn't exist) and `_release_reservation_by_ticket` issue raw `UPDATE`s. `resolve_order_intent` exists precisely to stop free-form overwrites. | By reading. | Route every transition through `AgentOperations.resolve_order_intent`, and add CLOSED if the design needs it. |
| **R6** | **Evidence checks are dead code.** The real `ValidationResult` has no `setup_key` and no `created_at`, so the setup-binding and freshness checks never run. Any object with `approved=True` passes, and a timestamp parse error is swallowed (fail open). | By reading (`trading_safety.py:74`). | Add `setup_key` and `created_at` to `ValidationResult` and stamp them in `validate()`. Make both checks mandatory in LIVE and DEMO. |

### Blockers: exits and modifies
| ID | Finding | Evidence | Fix |
|---|---|---|---|
| **X1** | **An SL-only modify clears the TP, and a TP-only modify removes the protective SL.** `modify_protection` sends `float(new_sl or 0.0)` and `float(new_tp or 0.0)`. | Probes P3, P3b. | Default each missing value to the position's current value. Never send `sl=0` for an owned position. |
| **X2** | **Wrong-position close.** When `positions_get(ticket=…)` raises `TypeError`, the adapter falls back to `positions[0]`, i.e. a *different* position, and uses that position's volume and side for the requested ticket. | Probe P6: closing ticket 5 sent volume 1.0 taken from ticket 99 (GBPUSD). | If the ticket isn't matched, refuse. Never fall back to index 0. |
| **X3** | **Exit requests lost `type_filling`, `deviation`, `magic` and `type_time`.** Before Phase 2, `emergency_market_exit`, `_close_position` and the partial closes built full requests with `_broker_market_filling_mode()`. The gateway rebuilds a bare request. Brokers that don't accept the default filling mode reject it (retcode 10030), so emergency exits and guardian flattens can fail live. | Probe P7. This is also a characterization change that wasn't called out, which the exit gate requires. | `submit_exit` should accept the caller's prepared request (as `submit_entry` does with `mt5_request`), or the adapter should compute the filling mode. |
| **X4** | **`MarketDataProvider.place_market_order` crashes.** It now passes `validation_result=`, `setup_key=`, `risk_amount=` and `intent_key=` to `order_execution.place_market_order`, which doesn't accept them, so every call raises `TypeError`, paper included. | Probe P8 (signature check). WIP only. | Remove the kwargs, or accept and ignore them on the paper path. |

### Design deviations (by reading)
| ID | Finding |
|---|---|
| **D1** | **Transport is switched per call on a shared singleton.** The plan says the adapter is "chosen ONCE at boot from the journal mode, never per call". Instead, `TradeManager._gateway()` calls `ensure_paper_transport` or `ensure_live_transport` on every exit or modify, and **re-configures the process-wide gateway** when the store path differs. The guardian's flatten calls `ensure_live_transport()` on the shared gateway too. In a paper session that has a terminal connected, a guardian flatten flips the shared gateway to live. Paper and live behaviour now depends on which caller ran last. |
| **D2** | **Paper entries don't go through the gateway at all.** `place_pending_order` returns from `PaperBroker` before reaching it, so there is no reservation and no intent lifecycle in paper. The concurrency invariant (CC-F1) is proven only for the gateway's unit tests, not for the paper path. |
| **D3** | **The reservation checks open risk only.** The plan's step 3 also re-checks daily and weekly loss, drawdown and **bucket** risk inside the transaction. Those still run only in the earlier, non-atomic `arbiter.permit`. |
| **D4** | **A crash between reservation and send leaves CREATED forever.** That fails safe (over-reserves), but nothing sweeps stale CREATED intents. Add an age-based sweep to UNRESOLVED during reconciliation. |
| **D5** | **The flattening flag is per gateway instance.** It's correct only while every caller shares the default instance (see D1). |
| **D6** | **The web GUI (`gui/`) has no login.** With `GUI_STRICT_AUTH=1` as the default, and the WIP removing the cookie set on `/`, every web GUI request now gets 401. The plan's same-origin cookie also assumed `gui_api` serves the GUI, but `/` returns JSON and the GUI runs on Vite. You need either a login screen that posts to `/api/auth/login`, or `gui_api` serving `gui/dist` and setting the cookie there. |
| **D7** | **The trades-reader lint ratchet was loosened.** In `test_from_trades_lint.py`, `core/order_gateway.py` was added and the `trading_safety.py` budget went from 10 to 11. That's acceptable, but it should be called out. |

### What is verified good
- **AST single-door test:** `order_send` appears only in `core/order_gateway.py`, and the adapters can't be imported elsewhere. There is also a runtime guard with a context variable.
- **PR 2.1:**
  - deck MCP allowlist (`mcp.ts:95`);
  - RL bridge never changes `action`;
  - decision toolkit allowlist (`llm_orchestrator.py:1809-1812`);
  - `execute_dynamic_metric_analysis` is out of the registry (helper kept, not a tool);
  - Prometheus binds `127.0.0.1`;
  - outage promotion removed.
- **PR 2.4:** the backtest journal guard resolves symlinks and refuses `trading_state.db`, the env journal and LIVE/DEMO scopes.
- **PR 2.5:**
  - Host check;
  - `hmac.compare_digest`;
  - no query-token auth;
  - reads are authenticated by default;
  - separate engine token passed to the engine subprocess;
  - the actor comes from the session.
- **Margin TOCTOU re-check (1a-F19):** keyword arguments are now correct and it fails closed.
- **Pyramiding:** refused in paper, and in live it can't pass the gateway without evidence.
- **Phase 1 strict xfails:** CC-F1 and 1a-F8 were converted to passes.

## Recommended order of fixes
1. R1 and R5 together (intent lifecycle), then R3, R2, R4, R6.
2. X1 to X4.
3. D1 (one transport at boot, no reconfiguration from `TradeManager` or the guardian), then D6.
4. Re-run the probes. They become permanent tests under `tests/safety/test_order_gateway_ledger.py`.
5. Then re-tick the ROADMAP.

## Fix status (same day)
| Item | Status |
|---|---|
| R1 | **Fixed.** `trading_safety.reserving_intent_payloads` is the one rule for the gateway, `open_risk` and `open_bucket_risk`. CREATED, PENDING, PARTIAL and UNRESOLVED reserve. FILLED and ERROR reserve only while ticketed and absent from `trades`. Any intent whose ticket has a trades row (open or closed) is skipped. This also fixes `open_bucket_risk` double-counting pending intents of open trades. |
| R2 | **Fixed.** `OrderIntent.account_equity`, then the live `account_info().equity` (LIVE/DEMO), then the store's `equity`; with none of these the entry is refused. |
| R3 | **Fixed.** A pre-existing CREATED intent is reused (no replace, metadata kept) and excluded from the sum. Any other existing state is refused as a duplicate. |
| R4 | **Fixed.** Release happens on a confirmed cancel only; otherwise the intent goes to UNRESOLVED and keeps reserving. |
| R5 | **Fixed.** Every gateway transition is checked against `AgentOperations.ORDER_INTENT_TRANSITIONS`. The `CLOSED` pseudo-state is gone. |
| R6 | **Fixed.** `ValidationResult.setup_key` and `created_at` added. The orchestrator binds `setup_key`. In LIVE/DEMO both are mandatory, and an unreadable timestamp is refused. |
| X1 | **Fixed.** Missing SL/TP keep the current values (from the caller or the position). A modify that would leave SL 0 is refused. |
| X2 | **Fixed.** Exact ticket match only; volume is bounded by the position's volume. |
| X3 | **Fixed.** The close request carries `type_filling` (broker flags), `deviation`, `magic` and `type_time`. |
| X4 | **Fixed.** `place_market_order` accepts the evidence kwargs. |
| D1 | **Fixed.** `ensure_*_transport` were removed. `TradeManager` uses the shared gateway only when its store and transport match, otherwise a private one. The guardian flattens through a private live gateway while holding the shared gateway's `flattening` flag. |
| Extra | Transient broker retcodes leave the intent CREATED for the caller's retry, and `abandon_entry` releases it once retries are exhausted (CREATED only). DONE_PARTIAL (10010) is now accepted and recorded as PARTIAL; it used to be recorded as REJECTED. |
| D2, D3, D4, D6 | See the follow-up below. |
| D5, D7 | Accepted. D5 is covered by D1; D7 is a documented ratchet change (the gateway is now at 1 site). |

## Follow-up: D2, D3, D4, D6
| Item | Status |
|---|---|
| D2 | **Fixed.** Paper entries that carry validation evidence go through `_paper_entry_via_gateway`: the shared paper gateway, or a private one on the same journal. The gateway never marks FILLED; the reconciler owns PENDING → FILLED in paper too, using the paper book's position as fill proof, and writes the trades row that takes over the reservation. Evidence-less calls (tools, tests) stay on the simulator. |
| D2 (found while testing) | **Fixed.** `reconcile_pending_orders` crashed on every fill at `TradeProposal(**payload)`, because `log_proposal` adds `validation_trace`. Fills were never journaled as trades, live included. Pinned by `tests/safety/test_paper_fill_reconcile.py`. |
| D3 | **Analysed, partly closed.** Only quantities that entries themselves change can be raced: open risk (atomic now) and bucket risk, which is bounded by the open-risk check because it uses the same fraction. Daily/weekly loss and drawdown aren't changed by entries. The rolling-correlation (2 %) and netting limits remain non-atomic and blind to pending intents → ROADMAP `D3b` (Phase 4). |
| D4 | **Fixed.** `sweep_stale_created` (default 5 min) runs at the start of each reconcile. A send that raises is UNRESOLVED on live (keeps reserving) and REJECTED on paper. |
| D6 | **Fixed and verified in a browser:** sign-in screen → cookie → data loads; curl checks for 401, wrong Host and the public bundle. |
| New: PH2-N1 | **Fixed (operator approved).** The authority is the sandbox `mt5_trader` mounts for `--live`, handed over only after its fail-closed ORDER_WRITE gate. The orchestrator mints a 2-minute `broker`-scoped token per live entry, only when the journal is LIVE; its own fallback harness context never grants authority. The live urgency path now has the same gate. Pinned by `tests/safety/test_live_order_authority.py`. |
