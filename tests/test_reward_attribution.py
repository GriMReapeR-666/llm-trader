from pathlib import Path

import pytest

from operations.reward_attribution import (
    causal_credit_shares,
    eligible_edge_ids,
    enrich_trade_result_attribution,
    extract_attribution_ids,
    lifecycle_qualifies_for_causal_credit,
    match_open_candidate_id,
    opportunity_precision_reward,
    score_edges_from_ledger,
    score_opportunities_from_ledger,
)
from operations.reward_ledger import emit_terminal_trade_rewards, list_reward_events
from operations.reward_sustainability import cvar, cvar_mixture_objective, throttle_agreement_reward


def test_lifecycle_gate_replay_pass():
    assert lifecycle_qualifies_for_causal_credit("REPLAY_PASS")
    assert lifecycle_qualifies_for_causal_credit("FULL")
    assert not lifecycle_qualifies_for_causal_credit("STAT_PASS")
    assert not lifecycle_qualifies_for_causal_credit("CANDIDATE")


def test_extract_and_enrich_attribution_ids():
    payload = {
        "proposal_features": {
            "causal_edge_ids": [3, 9],
            "opportunity_candidate_id": "cand-abc",
        }
    }
    extracted = extract_attribution_ids(payload)
    assert extracted["causal_edge_ids"] == [3, 9]
    assert extracted["candidate_id"] == "cand-abc"
    assert extracted["execution_authority"] is False

    class _Store:
        def trade_payload(self, ticket):
            return payload

    enriched = enrich_trade_result_attribution({"ticket": 1, "pnl": 1}, _Store())
    assert enriched["causal_edge_ids"] == [3, 9]
    assert enriched["candidate_id"] == "cand-abc"


def test_eligible_edge_ids_filters_lifecycle():
    ids = eligible_edge_ids(
        [1, 2, 3],
        lifecycle_lookup=lambda e: {1: "STAT_PASS", 2: "REPLAY_PASS", 3: "CANDIDATE"}[e],
    )
    assert ids == [2]


def test_emit_gates_causal_credit_on_lifecycle(tmp_path: Path):
    path = str(tmp_path / "attr.db")
    trade = {
        "ticket": 99,
        "pnl": 10.0,
        "risk": 10.0,
        "r_multiple": 1.0,
        "broker_reconciled": True,
        "causal_edge_ids": [1, 2],
        "candidate_id": "c1",
    }
    recorded = emit_terminal_trade_rewards(
        path,
        trade,
        lifecycle_lookup=lambda e: "STAT_PASS" if e == 1 else "REPLAY_PASS",
    )
    assert recorded["status"] == "RECORDED"
    assert recorded["eligible_causal_edge_ids"] == [2]
    events = list_reward_events(path, ticket=99)
    causal = [e for e in events if e["action"] == "causal_edge_credit"]
    assert len(causal) == 1
    assert causal[0]["edge_id"] == 2
    assert causal[0]["value"] == pytest.approx(0.10 * 1.0)


def test_offline_scorers(tmp_path: Path):
    path = str(tmp_path / "score.db")
    emit_terminal_trade_rewards(
        path,
        {
            "ticket": 7,
            "pnl": 20.0,
            "risk": 10.0,
            "r_multiple": 2.0,
            "broker_reconciled": True,
            "causal_edge_ids": [11],
            "candidate_id": "opp-1",
        },
    )
    edges = score_edges_from_ledger(path)
    assert edges["execution_authority"] is False
    assert edges["edge_count"] >= 1
    assert edges["edges"][0]["edge_id"] == 11
    opps = score_opportunities_from_ledger(path)
    assert opps["candidate_count"] >= 1
    assert opps["candidates"][0]["candidate_id"] == "opp-1"


def test_opportunity_precision_and_match():
    out = opportunity_precision_reward(
        approved=True, r_net=1.5, emitted=True, rejected_for_quality=False
    )
    assert out["value"] == pytest.approx(0.15 * 1.5)
    fp = opportunity_precision_reward(
        approved=False, r_net=0.0, emitted=True, rejected_for_quality=True
    )
    assert fp["value"] < 0
    matched = match_open_candidate_id(
        [
            {"candidate_id": "a", "symbol": "EURUSD", "setup_id": "x", "side_hint": "BUY", "score": 1},
            {"candidate_id": "b", "symbol": "EURUSD", "setup_id": "x", "side_hint": "BUY", "score": 5, "emitted_at": "z"},
        ],
        symbol="EURUSD",
        setup_id="x",
        side="BUY",
    )
    assert matched == "b"


def test_causal_credit_shares_split():
    rows = causal_credit_shares(2.0, [1, 2], agent_share=0.10)
    assert len(rows) == 2
    assert rows[0]["value"] == pytest.approx(0.10)
    assert rows[0]["execution_authority"] is False


def test_cvar_mixture_and_throttle():
    out = cvar([-2, -1, 0, 1, 2], alpha=0.4)
    assert out["status"] == "OK"
    assert out["cvar"] is not None
    mix = cvar_mixture_objective([1.0, 1.0, -4.0], alpha=0.34, mean_weight=0.5)
    assert mix["status"] == "OK"
    assert mix["objective"] < mix["mean"]
    assert mix["execution_authority"] is False
    agree = throttle_agreement_reward(degradation=0.4, throttle_applied=True)
    assert agree["value"] > 0
    miss = throttle_agreement_reward(degradation=0.4, throttle_applied=False)
    assert miss["value"] < 0
