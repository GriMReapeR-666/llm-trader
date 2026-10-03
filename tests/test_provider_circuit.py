from datetime import datetime, timezone

from provider_circuit import ProviderCircuitBreaker


class _Store:
    def __init__(self, state=None):
        self.state = state

    def get_state(self, key, default):
        return self.state if self.state is not None else default

    def set_state(self, key, value):
        self.state = value


def test_malformed_provider_circuit_state_fails_closed_until_manual_reset():
    store = _Store({
        "tier": "not-a-tier",
        "failures": ["not-a-timestamp"],
        "cooldown_until": "2026-08-09T10:00:00",
    })
    circuit = ProviderCircuitBreaker(store)
    status = circuit.status()
    assert status["mode"] == "MANUAL_RESET_REQUIRED"
    assert status["state_error"]
    assert circuit.can_call()[0] is False
    circuit.record(True, "should not clear malformed state")
    assert circuit.status()["mode"] == "MANUAL_RESET_REQUIRED"

    reset = circuit.reset()
    assert reset["mode"] == "AVAILABLE"
    assert "state_error" not in reset


def test_provider_circuit_accepts_timezone_aware_state_and_cools_down():
    now = datetime.now(timezone.utc)
    store = _Store({
        "tier": 2,
        "failures": [now.isoformat()],
        "cooldown_until": (now.replace(microsecond=0)).isoformat(),
        "downgraded_at": now.isoformat(),
        "last_success_at": now.isoformat(),
    })
    status = ProviderCircuitBreaker(store).status()
    assert "state_error" not in status
    assert status["mode"] in {"AVAILABLE", "COOLDOWN"}
