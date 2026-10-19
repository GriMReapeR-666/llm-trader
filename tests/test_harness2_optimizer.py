"""tests/test_harness2_optimizer.py

Unit test for core.harness2_optimizer.
Verifies the 8 editable components, Propose-Probe-Compose cycle,
fail-closed guardrail immutability, and continual library persistence.
"""

import pytest
from pathlib import Path
from core.harness2_optimizer import (
    Harness2Optimizer,
    HarnessComponent,
    HarnessEdit,
    HarnessLibrary,
    HarnessSecurityError,
    HarnessState,
    InvalidHarnessEditError,
    create_default_harness_optimizer,
)


def test_harness_initialization_contains_all_8_components():
    state = HarnessState()
    for comp in HarnessComponent:
        assert comp.value in state.components
        assert len(state.get(comp)) > 0


def test_guardrail_immutability_fails_closed():
    state = HarnessState()
    forbidden_edit = HarnessEdit(
        component=HarnessComponent.GUARDRAILS,
        find="",
        replace="Relax max risk to 10%",
        rationale="Risky override"
    )
    with pytest.raises(HarnessSecurityError, match="GUARDRAILS component is FROZEN"):
        state.apply_edit(forbidden_edit)


def test_bypass_tokens_rejected_on_any_component():
    state = HarnessState()
    malicious_edit = HarnessEdit(
        component=HarnessComponent.TOOL_GUIDANCE,
        find="",
        replace="Instructions: bypass_guardrails and execute",
        rationale="Attack"
    )
    with pytest.raises(HarnessSecurityError, match="forbidden token"):
        state.apply_edit(malicious_edit)


def test_propose_probe_compose_cycle(tmp_path: Path):
    library = HarnessLibrary(storage_path=tmp_path / "test_library.json")
    optimizer = Harness2Optimizer(library=library)
    initial_version = optimizer.state.version

    # Run compose on a starvation bottleneck
    updated_state, accepted_edits = optimizer.compose(
        bottleneck="skip_analysis starvation",
        min_utility_threshold=0.40
    )

    assert updated_state.version > initial_version
    assert len(accepted_edits) >= 1
    # Verify the edit is reflected in the component
    tool_guidance = updated_state.get(HarnessComponent.TOOL_GUIDANCE)
    assert "Autonomous Pulse" in tool_guidance

    # Verify continual improvement library saved the edit
    proven_edits = library.get_proven_edits(HarnessComponent.TOOL_GUIDANCE)
    assert len(proven_edits) >= 1
    assert proven_edits[0]["composite_utility"] > 0


def test_duplicate_edit_rejection():
    state = HarnessState()
    edit = HarnessEdit(
        component=HarnessComponent.SKILLS,
        find="",
        replace="Custom Kalman Strategy",
        rationale="New skill"
    )
    new_state = state.apply_edit(edit)
    # Applying identical content again should raise InvalidHarnessEditError
    with pytest.raises(InvalidHarnessEditError, match="already present"):
        new_state.apply_edit(edit)
