"""Tests for deterministic opportunity fusion (TA ∩ causal ∩ event)."""
from opportunity_fusion import score_opportunity, fusion_block_reasons, is_macro_setup


def test_macro_blocks_without_causal_agree():
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="BUY",
        symbol="EURUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=[],
        ta_eligible=True,
    )
    assert fusion["status"] == "BLOCK"
    assert fusion["execution_authority"] is False
    assert fusion["causal_agree"] is False
    assert fusion_block_reasons(fusion)


def test_macro_blocks_on_event_blackout_even_with_edges():
    edges = [{
        "edge_id": 7, "status": "APPROVED", "sign": "POSITIVE",
        "source_name": "EUR", "target_name": "USD",
    }]
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="BUY",
        symbol="EURUSD",
        event_state={"blackout": True, "reasons": [], "relevant_events": [{"phase": "PRE_RELEASE"}]},
        causal_edges=edges,
        ta_eligible=True,
    )
    assert fusion["status"] == "BLOCK"
    assert fusion["event_ok"] is False
    assert fusion["causal_agree"] is True


def test_macro_pass_with_agreeing_edge_and_clear_event():
    edges = [{
        "edge_id": 11, "status": "AUTO_APPROVED", "sign": "POSITIVE",
        "source_name": "POLICY_RATE", "target_name": "EUR",
        "confidence": 0.8,
    }]
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="BUY",
        symbol="EURUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=edges,
        ta_eligible=True,
        ta_confidence=0.7,
        adx=28,
    )
    assert fusion["status"] == "PASS"
    assert fusion["score"] > 0.5
    assert 11 in fusion["agreeing_edge_ids"]
    assert fusion["execution_authority"] is False


def test_non_macro_missing_edges_is_weak_not_block():
    fusion = score_opportunity(
        setup_id="trend_pullback_v1",
        side="SELL",
        symbol="GBPUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=[],
        ta_eligible=True,
    )
    assert fusion["status"] == "WEAK"
    assert fusion["macro_setup"] is False
    assert not fusion_block_reasons(fusion)


def test_sell_requires_negative_sign():
    edges = [{"edge_id": 1, "status": "STAT_PASS", "sign": "POSITIVE", "target_name": "XAUUSD"}]
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="SELL",
        symbol="XAUUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=edges,
        ta_eligible=True,
    )
    assert fusion["status"] == "BLOCK"
    assert fusion["causal_agree"] is False

    edges_neg = [{"edge_id": 2, "status": "STAT_PASS", "sign": "NEGATIVE", "target_name": "GOLD"}]
    ok = score_opportunity(
        setup_id="macro_continuation_v1",
        side="SELL",
        symbol="XAUUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=edges_neg,
        ta_eligible=True,
    )
    assert ok["status"] == "PASS"
    assert ok["causal_agree"] is True


def test_malformed_edge_list_fail_closed_for_macro():
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="BUY",
        symbol="EURUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges="not-a-list",
        ta_eligible=True,
    )
    assert fusion["status"] == "BLOCK"
    assert is_macro_setup("macro_foo_v1")


def test_macro_pass_via_multi_hop_propagation_without_direct_edges():
    prop = {
        "status": "OK",
        "path_id": "cpath:abc123",
        "bias": "BUY",
        "score": 0.42,
        "hops": [{"edge_id": 1}, {"edge_id": 2}],
        "execution_authority": False,
    }
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="BUY",
        symbol="EURUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=[],
        causal_propagation=prop,
        ta_eligible=True,
        ta_confidence=0.6,
        adx=22,
    )
    assert fusion["status"] == "PASS"
    assert fusion["causal_agree"] is True
    assert fusion["edge_agree"] is False
    assert fusion["propagation_agree"] is True
    assert fusion["causal_path_id"] == "cpath:abc123"
    assert fusion["execution_authority"] is False


def test_propagation_wrong_bias_does_not_unlock_macro():
    prop = {
        "status": "OK",
        "path_id": "cpath:sellpath",
        "bias": "SELL",
        "score": 0.9,
        "hops": [{"edge_id": 9}],
        "execution_authority": False,
    }
    fusion = score_opportunity(
        setup_id="macro_continuation_v1",
        side="BUY",
        symbol="EURUSD",
        event_state={"blackout": False, "reasons": []},
        causal_edges=[],
        causal_propagation=prop,
        ta_eligible=True,
    )
    assert fusion["status"] == "BLOCK"
    assert fusion["propagation_agree"] is False
    assert fusion["causal_agree"] is False
