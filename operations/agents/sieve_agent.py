"""Sieve scrape-poll executor (ResearchAgent principal).

The Sieve client keeps run state in a durable sqlite table, but the API is
asynchronous: a started run must be polled to completion.  Without a consumer
the sessions would sit in ``queued`` forever and a crash mid-run would never
resume.  This executor is that consumer.

It runs on the existing maintenance scheduler (``sieve_poll``, 60s cadence) and
only ever polls already-started sessions — it never starts a run, so a broken
Sieve API can never spend credits from the trading loop.  When
``SIEVE_API_KEY`` is absent the handler returns SKIPPED without touching the
network, so behavior is unchanged for unconfigured deployments.

Runs under the ResearchAgent principal (read-only with respect to trading:
zero order authority).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger("SieveAgent")

# Bounded work per invocation: the schedule re-fires every 60s, so polling in
# small slices keeps the handler well inside its budget.
POLL_LIMIT = 5
# Wall-clock ceiling so the handler stays well inside the scheduler's 45s
# default per-handler budget even when several sessions must be polled.
HANDLER_DEADLINE_S = 20.0


def handle_sieve_poll(payload: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Resume/poll due Sieve runs.  No-op (SKIPPED) when not configured."""
    from core import sieve_client

    if not sieve_client.is_configured():
        return {"status": "SKIPPED", "reason": "sieve_not_configured",
                "execution_authority": False}

    limit = POLL_LIMIT
    if isinstance(payload, dict):
        try:
            limit = max(1, int(payload.get("limit") or POLL_LIMIT))
        except (TypeError, ValueError):
            limit = POLL_LIMIT

    runner = sieve_client.SieveRunner()
    results = runner.resume(limit=limit, deadline_s=HANDLER_DEADLINE_S)
    completed = sum(1 for item in results if item.get("state") == "done")
    refused = sum(1 for item in results if item.get("state") == "refused")
    failed = sum(1 for item in results if item.get("last_error"))
    summary = {
        "status": "OK",
        "polled": len(results),
        "completed": completed,
        "refused": refused,
        "counts": runner.store.status_counts(),
        "execution_authority": False,
    }
    logger.info("sieve poll: polled=%d completed=%d refused=%d failed=%d",
                len(results), completed, refused, failed)
    return summary
