"""Tests: critical economic-dynamics.txt chains are present in machine SoT."""
from intelligence.economic_dynamics_catalog import (
    CRITICAL_DOC_CHAINS,
    coverage_report,
    resolve_entity_alias,
    validate_catalog,
)


def test_free_data_bindings_and_critical_chains_covered():
    report = coverage_report()
    assert report["execution_authority"] is False
    assert report["missing_binding_entities"] == []
    assert report["missing_critical_chains"] == []
    assert report["status"] == "OK"
    assert len(CRITICAL_DOC_CHAINS) >= 10


def test_catalog_still_valid_after_coverage_edges():
    problems = validate_catalog()
    assert problems == []


def test_resolve_entity_alias():
    assert resolve_entity_alias("XAUUSD") == "GOLD"
    assert resolve_entity_alias("FED_FUNDS") == "POLICY_RATE"
    assert resolve_entity_alias("unknown_xyz") is None
