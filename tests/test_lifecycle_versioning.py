import pytest

import llm_orchestrator as orch
from setup_registry import SETUPS


def test_setup_registry_fingerprint_rotates_on_rule_change():
    base = orch.setup_registry_fingerprint()
    assert isinstance(base, str) and len(base) == 64

    setup_id = next(iter(SETUPS))
    original_rr = SETUPS[setup_id].min_rr
    try:
        object.__setattr__(SETUPS[setup_id], "min_rr", original_rr + 0.5)
        changed = orch.setup_registry_fingerprint()
        assert changed != base
    finally:
        object.__setattr__(SETUPS[setup_id], "min_rr", original_rr)
    assert orch.setup_registry_fingerprint() == base


def test_prompt_template_constant_is_stable_and_hashed():
    assert "World-Class Hedge Fund Trader" in orch.SIGNAL_SYSTEM_PROMPT_TEMPLATE
    digest = orch.stable_content_hash(orch.SIGNAL_SYSTEM_PROMPT_TEMPLATE)
    assert len(digest) == 64
    # Same content must hash identically across calls.
    assert orch.stable_content_hash(orch.SIGNAL_SYSTEM_PROMPT_TEMPLATE) == digest


def test_get_decision_uses_versioned_template_not_inline_literal():
    """The live decision path must reference the constant, not a private copy."""
    import inspect
    source = inspect.getsource(orch.LLMOrchestrator.get_decision)
    assert "SIGNAL_SYSTEM_PROMPT_TEMPLATE" in source
    assert "World-Class Hedge Fund Trader" not in source
    assert orch.SIGNAL_SYSTEM_PROMPT_TEMPLATE.startswith(
        "You are a World-Class Hedge Fund Trader")
