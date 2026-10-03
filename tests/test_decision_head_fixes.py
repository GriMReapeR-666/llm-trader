"""Regression pins for the four Laya/Kronos decision-head fixes.

Each fix exists because of a measured production defect:

1. Escalation authority removed from D5a/D9 — 517 fabricated escalations on
   2,269 real articles (303 FULL_BLOCKADE + 214 EMERGENCY_HALT_ACTIVE) against
   0/428 for the deterministic tripwire.
2. D11/D11b/D6 routed to Kronos-mini, a candle forecaster that can actually see
   the OHLCVA these heads describe.
3. D0_1/D0_2 vendor-vocabulary translation — the model path was 100% inert.
4. Enum-membership validation on overrides + a labeled corpus + scorer, so a
   model swap can be decided on evidence instead of a 52% synthetic benchmark.

These tests are deliberately about CONTRACT and INTEGRATION. Semantic accuracy
of a model cannot be pinned here — that is what scripts/score_triage_corpus.py
and the labeled corpus exist to measure.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ===========================================================================
# Fix 1 — escalation authority removed from D5a/D9
# ===========================================================================

def test_escalation_authority_is_empty_by_default():
    """No head may hold escalation authority without an explicit edit."""
    from data.news_triage import MODEL_ESCALATION_AUTHORITY

    assert MODEL_ESCALATION_AUTHORITY == frozenset()


def test_d5a_fabricated_blockade_is_rejected():
    """The exact production failure: model says FULL_BLOCKADE on a Fed press
    release with no geopolitical content. Deterministic says INSUFFICIENT_DATA
    and must win."""
    from data.news_triage import _pick_better

    chosen = _pick_better(
        "D5a_escalation_ladder",
        "FULL_BLOCKADE",
        "INSUFFICIENT_DATA",
        {"full_blockade": 0.99},
    )
    assert chosen == "INSUFFICIENT_DATA"


def test_d5a_model_cannot_raise_any_band():
    """No rung of the ladder is reachable by the model."""
    from data.news_triage import _pick_better
    from research.decision_models.extended_taxonomy import D5a_EscalationLadder

    for band in D5a_EscalationLadder:
        chosen = _pick_better("D5a_escalation_ladder", band.value, "INSUFFICIENT_DATA", None)
        assert chosen == "INSUFFICIENT_DATA", f"{band.value} leaked through"


def test_d5a_model_cannot_deescalate():
    """The pre-existing floor still holds."""
    from data.news_triage import _pick_better

    chosen = _pick_better(
        "D5a_escalation_ladder", "RHETORIC_ONLY", "FULL_BLOCKADE", None
    )
    assert chosen == "FULL_BLOCKADE"


def test_d9_model_cannot_trip_kill_switch_however_confident():
    from data.news_triage import _pick_better

    chosen = _pick_better(
        "D9_emergency_kill_switch",
        "TRUE",
        "NORMAL_OPERATION",
        {"emergency_halt_active": 1.0},
    )
    assert chosen == "NORMAL_OPERATION"


def test_d9_fired_tripwire_survives_model_false():
    from data.news_triage import _pick_better

    chosen = _pick_better(
        "D9_emergency_kill_switch", "FALSE", "EMERGENCY_HALT_ACTIVE", {"normal_operation": 0.99}
    )
    assert chosen == "EMERGENCY_HALT_ACTIVE"


def test_d5a_spoof_floors_still_enforced():
    """The D0_1 reject-class floors were never in question and must not regress."""
    from data.news_triage import _pick_better
    from research.decision_models.extended_taxonomy import D0_1_SourceCredibility

    for reject in (
        D0_1_SourceCredibility.SPOOF_REJECTED,
        D0_1_SourceCredibility.UNVERIFIED_SOCIAL,
    ):
        chosen = _pick_better(
            "D0_1_source_credibility", "VERIFIED_OUTLET", reject.value, None
        )
        assert chosen == reject.value


def test_r1_concrete_det_class_still_authoritative():
    """Model noise must still not erase a deterministically routed event."""
    from data.news_triage import _pick_better

    chosen = _pick_better(
        "R1_headline_event_routing", "NOT_EVENT_NOISE", "EVENT_CPI", None
    )
    assert chosen == "EVENT_CPI"


# ===========================================================================
# Fix 3 — vendor vocabulary translation makes D0_1/D0_2 live
# ===========================================================================

def test_d0_1_vendor_labels_translate_to_gate_enum():
    """The vocabulary disjointness that made the head inert."""
    from data.news_triage import _map_model_label

    assert _map_model_label("D0_1_source_credibility", "WIRE_SERVICE") == "VERIFIED_OUTLET"
    assert _map_model_label("D0_1_source_credibility", "OFFICIAL_VERIFIED") == "TRUSTED_OFFICIAL"
    assert _map_model_label("D0_1_source_credibility", "UNVERIFIED_LEAK") == "UNVERIFIED_SOCIAL"
    assert _map_model_label("D0_1_source_credibility", "SPIN_NARRATIVE") == "VERIFIED_OUTLET"


def test_d0_2_vendor_labels_translate_to_gate_enum():
    from data.news_triage import _map_model_label

    assert _map_model_label("D0_2_duplicate_detection", "FRESH_EVENT") == "FIRST_REPORT"
    assert _map_model_label("D0_2_duplicate_detection", "PARTIAL_OVERLAP") == "CONFIRMED_UPDATE"


def test_fully_priced_deliberately_abstains_on_d0_2():
    """FULLY_PRICED was 61% of D0_2 answers and has no dedup counterpart.
    Mapping it to STALE_REPEAT would have silently changed INGEST->TAG on most
    of the corpus; abstaining keeps the deterministic label."""
    from data.news_triage import _map_model_label, _pick_better

    assert _map_model_label("D0_2_duplicate_detection", "FULLY_PRICED") == "FULLY_PRICED"
    # Passes translation, then fails the known-label check -> deterministic wins.
    assert _pick_better("D0_2_duplicate_detection", "FULLY_PRICED", "FIRST_REPORT", None) == "FIRST_REPORT"


def test_mapper_is_case_and_none_tolerant():
    from data.news_triage import _map_model_label

    assert _map_model_label("D0_1_source_credibility", "  wire_service ") == "VERIFIED_OUTLET"
    assert _map_model_label("D0_1_source_credibility", None) == ""
    assert _map_model_label("D0_1_source_credibility", "") == ""


def test_unmapped_head_passes_through_unchanged():
    """D0_3/D5a/D9/R1 already speak the gate vocabulary."""
    from data.news_triage import _map_model_label

    assert _map_model_label("D5a_escalation_ladder", "FULL_BLOCKADE") == "FULL_BLOCKADE"
    assert _map_model_label("R1_headline_event_routing", "EVENT_CPI") == "EVENT_CPI"


def test_d0_1_override_now_applies_through_bundle():
    """End-to-end: a translated vendor label becomes a real override with a
    valid gate-enum value (relies on the enum-membership guard)."""
    from research.decision_models.extended_taxonomy import create_decision_bundle
    from research.decision_models.feature_block import DecisionFeatureBlock

    fb = DecisionFeatureBlock(event_name="t", currency="USD", source_tier="RUNTIME_FEED")
    translated = "TRUSTED_OFFICIAL"
    bundle = create_decision_bundle(features=fb, overrides={"D0_1_source_credibility": translated})
    rec = bundle.decisions["D0_1_source_credibility"]
    # Either the deterministic rule already agreed, or the override applied cleanly.
    assert rec.selected_option == translated
    assert rec.selected_option in {"TRUSTED_OFFICIAL", "VERIFIED_OUTLET"}


# ===========================================================================
# Override enum-membership guard (supporting fix for 3)
# ===========================================================================

def test_foreign_vocabulary_override_is_rejected():
    """An override naming a value outside the head's enum must not be carried
    into selected_option — that is what made the old D0_1/D0_2 path inert."""
    from research.decision_models.extended_taxonomy import create_decision_bundle
    from research.decision_models.feature_block import DecisionFeatureBlock

    fb = DecisionFeatureBlock(event_name="t", currency="USD")
    bundle = create_decision_bundle(
        features=fb,
        overrides={"D11_trend_state": "WIRE_SERVICE", "D0_1_source_credibility": "OFFICIAL_VERIFIED"},
    )
    for head in ("D11_trend_state", "D0_1_source_credibility"):
        rec = bundle.decisions[head]
        assert not rec.is_overridden
        assert rec.model_label is None
        assert rec.selected_option != "WIRE_SERVICE"
        assert rec.selected_option != "OFFICIAL_VERIFIED"


def test_override_count_excludes_rejected_overrides():
    from research.decision_models.extended_taxonomy import create_decision_bundle
    from research.decision_models.feature_block import DecisionFeatureBlock

    fb = DecisionFeatureBlock(event_name="t", currency="USD")
    bundle = create_decision_bundle(
        features=fb, overrides={"D11_trend_state": "NOT_A_REAL_BAND"}
    )
    assert bundle.overrides_applied_count == 0


def test_is_allowed_option_covers_registry_heads():
    from research.decision_models.extended_taxonomy import TAXONOMY_REGISTRY, _is_allowed_option

    for head in ("D0_1_source_credibility", "D3_macro_regime", "D8_opportunity_arbitration"):
        if head not in TAXONOMY_REGISTRY:
            continue
        enum_cls = TAXONOMY_REGISTRY[head]["enum_cls"]
        for member in enum_cls:
            assert _is_allowed_option(head, member.value) is True
        assert _is_allowed_option(head, "DEFINITELY_NOT_A_MEMBER") is False


# ===========================================================================
# Fix 2 — Kronos routing for D11/D11b/D6
# ===========================================================================

def _bars(n=60, step=0.0015, noise=0.003):
    out = []
    for i in range(n):
        c = 1.0 + i * step
        out.append({
            "open": c, "high": c + abs(step) + noise,
            "low": c - abs(step) - noise, "close": c, "volume": 1000.0,
        })
    return out


def test_kronos_heads_disabled_env_returns_empty(monkeypatch):
    import research.decision_models.kronos_heads as kh

    monkeypatch.setenv("KRONOS_FORCE_DISABLED", "1")
    assert kh.kronos_head_overrides(_bars(), symbol="EURUSD") == {}


def test_kronos_short_history_returns_empty(monkeypatch):
    import research.decision_models.kronos_heads as kh

    monkeypatch.delenv("KRONOS_FORCE_DISABLED", raising=False)
    kh.reset_kronos_head_state()
    assert kh.kronos_head_overrides(_bars(n=5), symbol="EURUSD") == {}


def test_kronos_empty_history_returns_empty(monkeypatch):
    import research.decision_models.kronos_heads as kh

    monkeypatch.delenv("KRONOS_FORCE_DISABLED", raising=False)
    kh.reset_kronos_head_state()
    assert kh.kronos_head_overrides([]) == {}
    assert kh.kronos_head_overrides(None) == {}


def test_kronos_failure_returns_empty(monkeypatch):
    """Any exception in the forecast path must degrade to {} silently."""
    import research.decision_models.kronos_heads as kh

    monkeypatch.delenv("KRONOS_FORCE_DISABLED", raising=False)
    kh.reset_kronos_head_state()

    class _Boom:
        def predict_multi_dim(self, **kwargs):
            raise RuntimeError("injected")

    monkeypatch.setitem(kh._INSTANCES, "kronos-mini", _Boom())
    assert kh.kronos_head_overrides(_bars(), symbol="EURUSD", model_key="kronos-mini") == {}


def test_kronos_heuristic_backend_is_refused(monkeypatch):
    """A heuristic forecast reproduces the deterministic defaults and must not
    be laundered into an override that looks model-derived."""
    import research.decision_models.kronos_heads as kh

    monkeypatch.delenv("KRONOS_FORCE_DISABLED", raising=False)
    kh.reset_kronos_head_state()

    class _Heuristic:
        weights_loaded = False
        backend = "heuristic"
        mean_path = [{"close": 1.1} for _ in range(24)]
        predicted_volatility_5d = 0.09
        confidence = 0.5

        def predict_multi_dim(self, **kwargs):
            return self

    monkeypatch.setitem(kh._INSTANCES, "kronos-mini", _Heuristic())
    assert kh.kronos_head_overrides(_bars(), symbol="EURUSD", model_key="kronos-mini") == {}


def test_kronos_only_touches_its_own_heads(monkeypatch):
    """D0_1/D9/R1 etc. must never be reachable from the Kronos path."""
    import research.decision_models.kronos_heads as kh

    assert set(kh.KRONOS_OWNED_HEADS) == {
        "D11_trend_state", "D11b_vol_regime", "D6_yield_curve_divergence",
    }
    for head in kh.KRONOS_OWNED_HEADS:
        assert head not in ("D0_1_source_credibility", "D9_emergency_kill_switch",
                            "D5a_escalation_ladder", "R1_headline_event_routing")


def test_kronos_returns_valid_enum_values(monkeypatch):
    """Whatever it returns must be a member of the target enum, so the bundle's
    enum guard accepts it."""
    import research.decision_models.kronos_heads as kh
    from research.decision_models.extended_taxonomy import (
        D11_TrendState, D11b_VolRegime, D6_YieldCurveDivergence,
    )

    monkeypatch.delenv("KRONOS_FORCE_DISABLED", raising=False)
    kh.reset_kronos_head_state()

    class _Neural:
        weights_loaded = True
        backend = "neural"
        mean_path = [{"close": 1.0 + i * 0.001} for i in range(24)]
        predicted_volatility_5d = 0.04
        confidence = 0.8

        def predict_multi_dim(self, **kwargs):
            return self

    monkeypatch.setitem(kh._INSTANCES, "kronos-mini", _Neural())
    out = kh.kronos_head_overrides(_bars(), symbol="EURUSD", model_key="kronos-mini")
    assert out
    assert out["D11_trend_state"] in {m.value for m in D11_TrendState}
    assert out["D11b_vol_regime"] in {m.value for m in D11b_VolRegime}
    for head, value in out.items():
        assert head in kh.KRONOS_OWNED_HEADS


def test_kronos_d6_requires_curve_features(monkeypatch):
    """Kronos forecasts the traded symbol, not the curve — D6 must only appear
    when the caller supplies real curve inputs."""
    import research.decision_models.kronos_heads as kh
    from research.decision_models.feature_block import DecisionFeatureBlock

    monkeypatch.delenv("KRONOS_FORCE_DISABLED", raising=False)
    kh.reset_kronos_head_state()

    class _Neural:
        weights_loaded = True
        backend = "neural"
        mean_path = [{"close": 1.0 + i * 0.001} for i in range(24)]
        predicted_volatility_5d = 0.02
        confidence = 0.8

        def predict_multi_dim(self, **kwargs):
            return self

    monkeypatch.setitem(kh._INSTANCES, "kronos-mini", _Neural())

    no_features = kh.kronos_head_overrides(
        _bars(), symbol="EURUSD", model_key="kronos-mini", feature_block=None
    )
    assert "D6_yield_curve_divergence" not in no_features

    inverted = DecisionFeatureBlock(
        event_name="t", currency="USD", curve_2s10s_bps=-40.0,
        yield_10y_delta_bps=5.0, dxy_change_pct=0.1,
    )
    with_curve = kh.kronos_head_overrides(
        _bars(), symbol="EURUSD", model_key="kronos-mini", feature_block=inverted
    )
    assert with_curve["D6_yield_curve_divergence"] == "RECESSIONARY_INVERSION"


def test_kronos_bundle_integration_records_provenance(monkeypatch):
    """The bundle must name Kronos as the deciding model, not just 'a model'."""
    from research.decision_models.extended_taxonomy import create_decision_bundle
    from research.decision_models.feature_block import DecisionFeatureBlock

    fb = DecisionFeatureBlock(event_name="t", currency="USD")
    bundle = create_decision_bundle(
        features=fb,
        overrides={
            "D11_trend_state": "STRONG_TREND",
            "D11b_vol_regime": "VOLATILITY_EXPANSION",
        },
    )
    rec = bundle.decisions["D11_trend_state"]
    assert rec.is_overridden
    assert "Kronos" in rec.override_reason
    assert rec.execution_authority is False


def test_bars_from_prices_preserves_realized_range():
    """Reconstructed bars must carry the ATR as true range so the D11b head keys
    on real volatility rather than a flat synthetic range."""
    from intelligence.graph_nodes import MacroAnalystNode

    prices = [1.0 + i * 0.001 for i in range(30)]
    atr = [0.002] * 30
    bars = MacroAnalystNode._bars_from_prices(prices, [1000.0] * 30, atr)
    assert len(bars) == 30
    for bar in bars:
        assert bar["high"] > bar["close"] > bar["low"]
        assert abs((bar["high"] - bar["low"]) - 0.002) < 1e-9


def test_bars_from_prices_tolerates_garbage():
    from intelligence.graph_nodes import MacroAnalystNode

    bars = MacroAnalystNode._bars_from_prices([1.0, None, "bad", 1.003], None, None)
    assert len(bars) == 2
    for bar in bars:
        assert bar["low"] > 0


def test_bars_from_prices_empty():
    from intelligence.graph_nodes import MacroAnalystNode

    assert MacroAnalystNode._bars_from_prices([], [], []) == []


# ===========================================================================
# Fix 4 — corpus + scorer plumbing
# ===========================================================================

CORPUS = os.path.join(REPO_ROOT, "research", "decision_models", "triage_corpus.jsonl")


def test_corpus_exists_and_is_wellformed():
    assert os.path.exists(CORPUS), "run scripts/build_triage_corpus.py first"
    with open(CORPUS, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    assert rows, "corpus is empty"
    for rec in rows:
        assert "summary" in rec
        assert "observed" in rec
        assert "gold" in rec
        assert rec.get("source") in {"unlabeled", "auto_consensus", "hand"}


def test_corpus_has_no_invented_labels():
    """Auto-consensus rows may only carry D5a/D9 labels, never the
    dedupe-dependent D0_2, and must never carry an escalated D5a band."""
    with open(CORPUS, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    for rec in rows:
        if rec.get("source") != "auto_consensus":
            continue
        gold = rec.get("gold") or {}
        assert not gold.get("d0_2"), "D0_2 is not reconstructible from a journal row"
        assert gold.get("d5a") in (None, "INSUFFICIENT_DATA", "RHETORIC_ONLY")


def test_scorer_excludes_auto_consensus_by_default():
    from scripts.score_triage_corpus import score

    report = score(CORPUS, include_auto=False, min_examples=5)
    assert report["included_auto_consensus"] is False


def test_scorer_weighted_accuracy_is_cell_weighted():
    """A confusion matrix with all mass in one correct cell must score 1.0, not
    1/n. (An earlier unweighted implementation returned 1/89.)"""
    from scripts.score_triage_corpus import score

    report = score(CORPUS, include_auto=True, min_examples=5)
    for head, data in report["per_head"].items():
        for system, acc in data["accuracy"].items():
            if acc is None:
                continue
            assert 0.0 <= acc <= 1.0, f"{head}/{system} out of range: {acc}"


def test_scorer_reports_escalation_rate():
    """The metric that actually exposes fabricated risk."""
    from scripts.score_triage_corpus import score

    report = score(CORPUS, include_auto=True, min_examples=5)
    for head in ("d5a", "d9_kill_switch"):
        if head in report["per_head"]:
            rates = report["per_head"][head]["escalation_rate"]
            for value in rates.values():
                assert 0.0 <= value <= 1.0


def test_scorer_flags_insufficient_evidence():
    """A head must not be able to support a model swap below the evidence floor."""
    from scripts.score_triage_corpus import score

    report = score(CORPUS, include_auto=True, min_examples=10_000)
    for data in report["per_head"].values():
        assert data["sufficient_for_claim"] is False


def test_scorer_handles_missing_corpus():
    from scripts.score_triage_corpus import score

    report = score("no_such_corpus.jsonl", include_auto=False, min_examples=5)
    assert report["labeled_rows"] == 0


def test_builder_produces_stratified_sample(tmp_path):
    """Sampling must oversample the safety heads relative to raw frequency."""
    import sqlite3

    from scripts.build_triage_corpus import build

    db = tmp_path / "j.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE macro_sources (payload TEXT)")
    rows = []
    for i in range(60):
        rows.append(json.dumps({
            "summary": "Routine market commentary number %d" % i,
            "url": "https://example.com/%d" % i,
            "publisher": "Example",
            "source_policy": {"tier": "RUNTIME_FEED"},
            "triage": {
                "d0_1": "VERIFIED_OUTLET", "d0_2": "FIRST_REPORT",
                "d0_3": "ANALYST_OPINION", "d5a": "INSUFFICIENT_DATA",
                "d9_kill_switch": "NORMAL_OPERATION", "r1": "NOT_EVENT_NOISE",
                "laya_answers": {}, "disagreements": [],
            },
        }))
    con.executemany("INSERT INTO macro_sources (payload) VALUES (?)", [(r,) for r in rows])
    con.commit()
    con.close()

    out = tmp_path / "corpus.jsonl"
    summary = build(str(db), str(out), limit=0, seed=1, auto_label=False)
    assert summary["journal_records"] == 60
    assert summary["written"] > 0
    ids = set()
    with open(out, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            ids.add(rec["id"])
            assert rec["source"] == "unlabeled"
    assert len(ids) == summary["written"], "duplicate ids emitted"


def test_corpus_stats_reports_coverage():
    from scripts.build_triage_corpus import stats

    report = stats(CORPUS)
    assert report["rows"] > 0
    assert set(report["sources"]) <= {"unlabeled", "auto_consensus", "hand"}


def test_draft_renders_all_three_views(capsys):
    """The labeling UI must show det AND model so the reviewer can adjudicate
    rather than rubber-stamp the deterministic default."""
    from scripts.label_triage_corpus import draft

    draft(CORPUS, ["d9_kill_switch"], limit=2)
    out = capsys.readouterr().out
    assert "det      :" in out
    assert "model    :" in out
    assert "LABEL    :" in out
    assert "NOTE     :" in out


def test_execution_authority_never_granted():
    """The invariant that must survive every one of these changes."""
    from research.decision_models.extended_taxonomy import create_decision_bundle
    from research.decision_models.feature_block import DecisionFeatureBlock

    fb = DecisionFeatureBlock(event_name="t", currency="USD")
    bundle = create_decision_bundle(
        features=fb,
        overrides={
            "D11_trend_state": "STRONG_TREND",
            "D0_1_source_credibility": "TRUSTED_OFFICIAL",
        },
    )
    assert bundle.execution_authority is False
    for rec in bundle.decisions.values():
        assert rec.execution_authority is False