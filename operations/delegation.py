"""Task-graph delegation: parent tasks spawn principal-owned child leases.

Thin, exception-safe wrapper over AgentOperations' durable primitives
(claim_task / heartbeat_task / link_tasks / complete_task / fail_task) so a
principal can delegate work as a child with correct edge rows and guaranteed
terminal status — success, failure, or abandonment (lease expiry + reclaim).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


class Delegator:
    def __init__(self, operations: Any):
        self.ops = operations

    @contextmanager
    def child(self, *, parent_key: str, key: str, principal: str,
              task_type: str, payload: Optional[Dict[str, Any]] = None,
              run_id: str = "", lease_seconds: int = 300,
              max_attempts: int = 3) -> Iterator[Dict[str, Any]]:
        """Spawn a linked child task; terminal state is always recorded.

        Usage:
            with delegator.child(parent_key=..., key=..., principal="EventAgent",
                                 task_type="triage", payload={...}) as task:
                ... work ...
                task["result"]["x"] = 1      # optional, persisted on complete
        Raises the underlying exception after fail_task() on error paths.
        """
        claimed = self.ops.claim_task(
            key, principal, task_type, payload or {},
            run_id=run_id, lease_seconds=lease_seconds, max_attempts=max_attempts,
        )
        if not claimed:
            # Deduplicated/retry-exhausted: treat as no-op for the caller.
            yield {"claimed": False, "key": key, "result": {}}
            return

        self._link_ensuring_parent(parent_key, key, principal, run_id)

        handle: Dict[str, Any] = {"claimed": True, "key": key, "result": {}}
        try:
            yield handle
            self.ops.complete_task(key, handle.get("result") or {})
        except Exception as exc:
            try:
                self.ops.fail_task(key, str(exc)[:500])
            except Exception:
                logger.exception("fail_task bookkeeping failed for %s", key)
            raise

    def _link_ensuring_parent(self, parent_key: str, child_key: str,
                              principal: str, run_id: str) -> None:
        """Link child→parent, materializing a placeholder parent if needed."""
        try:
            self.ops.link_tasks(parent_key, child_key)
        except ValueError:
            # Parent not yet claimed (e.g., cycle task lives elsewhere):
            # materialize an explicit placeholder so the edge graph stays whole.
            self.ops.claim_task(
                parent_key, principal, "delegation.parent",
                {"auto": "placeholder", "for_child": child_key},
                run_id=run_id, lease_seconds=3600, max_attempts=1,
            )
            self.ops.link_tasks(parent_key, child_key)


def spawn_fanout(delegator: Delegator, *, parent_key: str, items: List[Dict[str, Any]],
                 principal: str, task_type: str, key_fn: Callable[[Dict[str, Any]], str],
                 run_id: str = "") -> List[Dict[str, Any]]:
    """Create N pending children of one parent without executing them yet."""
    created: List[Dict[str, Any]] = []
    for item in items:
        key = key_fn(item)
        claimed = delegator.ops.claim_task(
            key, principal, task_type, item, run_id=run_id, lease_seconds=3600,
            max_attempts=1,
        )
        if claimed:
            delegator._link_ensuring_parent(parent_key, key, principal, run_id)
        created.append({"key": key, "claimed": claimed})
    return created
