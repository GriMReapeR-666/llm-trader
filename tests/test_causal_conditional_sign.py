"""Regression tests: CONDITIONAL/UNKNOWN causal edges must not be validated on
void directional evidence.

`_directional_hit` used to `return True` for any sign that was not POSITIVE or
NEGATIVE. Every valid reaction was therefore counted as agreement for a
CONDITIONAL edge, which produced a 100% empirical win rate by construction:
AUTO_APPROVED at the sample floor (and, in the reinforcement pass, a
confidence that could only ever rise). The sign carries no directional claim,
so those reactions are non-informative -- the same treatment this module
already gives an unmeasurable return window.
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.join(_ROOT, "tests")
for _p in [_ROOT, _TESTS] + [os.path.join(_ROOT, _sub) for _sub in
                             ("data", "core", "intelligence", "operations", "research", "ui")]:
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from autonomous_causal_engine import (
    MIN_REACTION_SAMPLES,
    AutonomousCausalValidator,
    _directional_hit,
)
from macro_graph import MacroGraph

from test_autonomous_causal_engine import _primary_source, _seed_reactions


def test_directional_hit_is_none_for_non_directional_signs():
    """Unit: CONDITIONAL/UNKNOWN carry no directional prediction."""
    assert _directional_hit("CONDITIONAL", 0.3, 0.0025) is None
    assert _directional_hit("UNKNOWN", 0.3, 0.0025) is None
    assert _directional_hit("CONDITIONAL", -0.3, -0.0025) is None
    # Directional signs are unaffected.
    assert _directional_hit("POSITIVE", 0.3, 0.0025) is True
    assert _directional_hit("POSITIVE", 0.3, -0.0025) is False
    assert _directional_hit("NEGATIVE", 0.3, -0.0025) is True
    assert _directional_hit("NEGATIVE", 0.3, 0.0025) is False


def test_conditional_edge_is_not_approvable_on_directional_evidence(tmp_path):
    """A full sample of valid reactions must not approve a CONDITIONAL edge.

    Before the fix this returned approved=True at a 100% empirical win rate --
    every reaction counted as a hit by construction.
    """
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="CONDITIONAL",
        confidence=0.90,
        source_id=source_id,
        notes="conditional edges must never auto-approve on market direction",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES,
                    return_fraction=0.0025)

    validator = AutonomousCausalValidator(graph)
    result = validator.validate_candidate_edge(edge_id)

    assert result["approved"] is False
    assert result["status"] == "INSUFFICIENT_SAMPLE"
    assert result["empirical_win_rate"] is None
    assert result["valid_samples"] == 0

    with graph._con() as con:
        status = con.execute(
            "SELECT status FROM macro_edges WHERE edge_id=?", (edge_id,)).fetchone()[0]
    assert status == "CANDIDATE"


def test_reinforcement_never_moves_a_conditional_edge(tmp_path):
    """The reinforcement pass must not confirm (or miss) a CONDITIONAL edge.

    All of its reactions are non-informative, so confirms and misses both stay
    zero and confidence is written back unchanged.
    """
    graph = MacroGraph(str(tmp_path / "macro.db"))
    source_id = _primary_source(graph)
    edge_id = graph.propose_edge(
        source_name="FED_RATE_HIKE",
        source_type="POLICY_RATE",
        target_name="USD_INDEX_RALLY",
        target_type="ASSET_REGIME",
        relation="DRIVES_CURRENCY_STRENGTH",
        sign="CONDITIONAL",
        confidence=0.90,
        source_id=source_id,
        notes="reinforcement must be a no-op for conditional signs",
    )
    _seed_reactions(graph, source_id, count=MIN_REACTION_SAMPLES,
                    return_fraction=0.0025)

    validator = AutonomousCausalValidator(graph)
    before = None
    with graph._con() as con:
        before = con.execute(
            "SELECT confidence FROM macro_edges WHERE edge_id=?", (edge_id,)).fetchone()[0]

    # Call the single-edge pass directly: expected_sign is passed explicitly,
    # so no status precondition applies.
    report = validator._reinforce_single_edge(
        edge_id, source_id, "CONDITIONAL", float(before), "CANDIDATE")

    with graph._con() as con:
        after = con.execute(
            "SELECT confidence FROM macro_edges WHERE edge_id=?", (edge_id,)).fetchone()[0]

    assert float(before) == float(after)
    assert report is None or report.get("confirms", 0) == 0
    assert report is None or report.get("misses", 0) == 0
