"""MaintenanceAgent / LedgerAgent persona: stale lockfile and orphaned WAL pruning, artifact housekeeping."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from operations.ledger_housekeeping import plan, apply

logger = logging.getLogger(__name__)


def handle_housekeeping(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Prune stale lockfiles, orphaned WALs, and old artifacts."""
    logger.info("MaintenanceAgent starting ledger housekeeping")
    try:
        cleanup_plan = plan()
        result = apply(cleanup_plan)
        logger.info("Ledger housekeeping complete: deleted=%d, failed=%d, freed_bytes=%d",
                    result.get("deleted", 0), result.get("failed", 0), result.get("freed_bytes", 0))
        return result
    except Exception as exc:
        logger.warning("Ledger housekeeping encountered an issue: %s", exc)
        return {"status": "error", "error": str(exc)}
