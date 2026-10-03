"""Persistent provider health circuit breaker; it never weakens trade safety."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple


class ProviderCircuitBreaker:
    DEFAULT_STATE = {
        "tier": 1, "failures": [], "downgraded_at": None,
        "cooldown_until": None, "last_error": "", "last_success_at": None,
    }

    def __init__(self, store, key: str = "llm_provider_circuit"):
        self.store, self.key = store, key

    def _state(self) -> Dict:
        raw = self.store.get_state(self.key, dict(self.DEFAULT_STATE))
        if not isinstance(raw, dict):
            return {**self.DEFAULT_STATE, "state_error": "provider circuit state is not an object"}
        state = {**self.DEFAULT_STATE, **raw}
        errors = []
        try:
            tier = int(state.get("tier"))
            if tier < 1 or tier > 4:
                errors.append("tier must be an integer from 1 to 4")
                state["tier"] = 4
            else:
                state["tier"] = tier
        except (TypeError, ValueError, OverflowError):
            errors.append("tier is malformed")
            state["tier"] = 4
        failures = state.get("failures")
        if not isinstance(failures, list):
            errors.append("failures must be a list")
            failures = []
        else:
            for value in failures:
                if self._time(value) is None:
                    errors.append("failure timestamp is malformed")
                    break
        for field in ("downgraded_at", "cooldown_until", "last_success_at"):
            value = state.get(field)
            if value is not None and self._time(value) is None:
                errors.append(f"{field} is malformed")
        state["failures"] = failures
        if errors:
            state["state_error"] = "; ".join(errors)
        else:
            state.pop("state_error", None)
        return state

    @staticmethod
    def _time(value: str | None):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    def status(self) -> Dict:
        state = self._state()
        now = datetime.now(timezone.utc)
        cooldown = self._time(state.get("cooldown_until"))
        state["cooldown_active"] = bool(cooldown and cooldown > now)
        state["mode"] = ("MANUAL_RESET_REQUIRED" if state.get("state_error") or int(state.get("tier", 4)) >= 4
                          else ("COOLDOWN" if state["cooldown_active"] else "AVAILABLE"))
        return state

    def can_call(self) -> Tuple[bool, str]:
        state = self.status()
        if int(state.get("tier", 1)) >= 4:
            return False, "provider circuit is deterministic-only after repeated failures"
        if state["cooldown_active"]:
            return False, f"provider cooldown active until {state['cooldown_until']}"
        return True, "available"

    def record(self, success: bool, error: str = "") -> Dict:
        now = datetime.now(timezone.utc)
        state = self._state()
        if state.get("state_error"):
            # Do not mutate an untrusted state record.  Manual reset is the
            # only path that can clear malformed persisted circuit state.
            return self.status()
        failures = [value for value in state.get("failures", []) if self._time(value) and self._time(value) >= now - timedelta(minutes=10)]
        if success:
            # Upgrade one tier only after an hour without a current failure.
            downgraded = state.get("downgraded_at")
            if downgraded and now - self._time(downgraded) >= timedelta(hours=1):
                state["tier"] = max(1, int(state.get("tier", 1)) - 1)
                state["downgraded_at"] = None
            state["failures"] = failures
            state["last_success_at"] = now.isoformat()
            state["cooldown_until"] = None
        else:
            failures.append(now.isoformat())
            state["failures"] = failures
            state["last_error"] = error[:500]
            if len(failures) >= 3:
                state["tier"] = min(4, int(state.get("tier", 1)) + 1)
                state["downgraded_at"] = now.isoformat()
                state["failures"] = []
                cooldown_seconds = {2: 60, 3: 300}.get(int(state["tier"]), 0)
                state["cooldown_until"] = (now + timedelta(seconds=cooldown_seconds)).isoformat() if cooldown_seconds else None
        self.store.set_state(self.key, state)
        return self.status()

    def reset(self, operator: str = "operator", reason: str = "manual reset") -> Dict:
        """Return from deterministic-only mode only through explicit operator action."""
        state = {**self.DEFAULT_STATE,
                 "last_error": "", "last_success_at": None, "reset_at": datetime.now(timezone.utc).isoformat(),
                 "reset_by": operator[:200], "reset_reason": reason[:500]}
        self.store.set_state(self.key, state)
        return self.status()
