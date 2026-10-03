# Trading System Incident Runbooks

These procedures are operational controls, not trading advice. The deterministic
permission gate and persistent kill switch take priority over recovery speed.

## Universal first response

1. Enable the persistent kill switch: `python mt5_trader.py kill-switch enable --reason "incident"`.
2. Confirm no new order intent is being submitted; do not delete journal rows.
3. Capture `journal integrity`, `journal alerts`, `journal execution-reviews`,
   `journal legacy-status`, and the relevant log/trace IDs.
4. Reconcile MT5 positions/orders before restarting or changing configuration.
5. Resume only after the incident owner records evidence and explicitly disables
   the kill switch.

## MT5 authorization or broker outage

- Treat `(-6, Terminal: Authorization failed)`, missing ticks, or failed account
  info as `NO_TRADE`.
- Verify terminal is open, logged into the intended server/account, and the
  symbol is visible/tradable. Never retry credentials blindly.
- Resolve pending order reviews against broker history; do not infer fills.
- Run broker preflight and daily reconciliation after connectivity returns.

## LLM/provider outage or quota exhaustion

- The provider circuit must enter cooldown/manual-reset mode; deterministic
  validation may continue for monitoring, but no new LLM proposal is trusted.
- Do not silently switch model/provider. Record provider, model, request ID,
  retry count, error, and estimated/actual cost.
- Reset only after a successful health check and operator review.

## Calendar/news outage or stale macro data

- Missing or malformed relevant calendar data is a no-trade condition during
  configured event windows. Do not treat missing data as a safe market.
- Preserve the raw response/error and source timestamp. Resume only after fresh
  data passes the data-quality gate.

## Database corruption or failed integrity check

- Keep the kill switch enabled and stop all writers.
- Copy the database and WAL files for evidence; do not run destructive repair.
- Verify the latest manifest with `journal backup-status --backup-path ...`.
- Restore to a new path using `journal backup-restore`; run integrity and audit
  checks before switching configuration.

## Stuck, duplicate, or unresolved order

- Do not submit a compensating order. Open an execution review and compare
  intent, order, deal, and position IDs with MT5 history.
- Resolve using `journal resolve-execution-review` only after broker evidence.
- Quarantine ambiguous tickets and keep them out of learning/promotion data.

## Bad/stale/manipulated market data

- Stop proposals when tick age, spread, candle continuity, DOM, or event data
  fails. Preserve the rejected snapshot and reason.
- Do not substitute a candle close for a missing quote or DOM snapshot.
- Re-enable only after a completed candle and fresh broker quote pass checks.

## Emergency stop or abnormal drawdown

- Enable the kill switch immediately; position management remains restricted to
  bot-owned magic numbers and broker-confirmed state.
- Reconcile realized P&L, exposure, margin/free-margin, and daily/weekly loss.
- Require a written operator reset with evidence; never auto-clear the switch.

## Recovery evidence

Record incident ID, start/end time, affected symbols, kill-switch state, broker
and provider status, unresolved tickets, database backup hash, reconciliation
result, operator, and the exact command/output used to resume.

