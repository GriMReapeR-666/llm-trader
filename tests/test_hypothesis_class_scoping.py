"""Hypothesis-class scoping of the S.Ξ multiple-testing bar.

The live ledger holds ~2.8k trials from every research programme in the
repo.  Deflating a new hypothesis against that whole family is *correct*
(audit 2026-10-20 §7) and useless: the luck bar sits at E[max|null] = 3.53
SE (the exact order statistic), so a real annualized Sharpe of 1.9 is
detected ~1% of the time.

The lever the audit names is SCOPING.  These tests pin the three properties
that make scoping honest rather than a loophole:

  1. **Canonical** — the family is derived from the hypothesis identity by
     one taxonomy, so a caller cannot under-report its family by forgetting
     a tag, and two gates inside one call cannot disagree.
  2. **Complete** — a class pool contains *every* trial of that family,
     including the rows recorded before the taxonomy existed.  A class bar
     computed against a fraction of its family is a bar that invents
     significance.
  3. **Fail-closed** — an identity that cannot be attributed to a family
     keeps the GLOBAL bar.  "general" is an observability bucket, never a
     licence to deflate against a small pool.

The last test in the file is the payoff: the calibration table's bar and
required Sharpe must move LEFT (down) once classes are used.
"""
from __future__ import annotations

import pytest

from core.hypothesis_class import (
    CLASSIFIER_VERSION,
    UNCLASSIFIED,
    classify,
    describe_class,
    effective_class,
    group_by_effective_class,
    known_classes,
    resolve_class,
    scope_of,
)
from core.trial_ledger import TrialLedger


@pytest.fixture
def ledger(tmp_path):
    return TrialLedger(db_path=str(tmp_path / "ledger.db"))


# ---------------------------------------------------------------------------
# 1. The taxonomy
# ---------------------------------------------------------------------------
class TestTaxonomy:
    def test_version_is_declared(self):
        assert CLASSIFIER_VERSION

    def test_known_classes_are_unique_and_described(self):
        classes = known_classes()
        assert len(classes) == len(set(classes))
        assert UNCLASSIFIED not in classes
        for class_id in classes:
            assert describe_class(class_id) != "Unknown hypothesis class."

    @pytest.mark.parametrize(
        "strategy_id,expected",
        [
            ("alpha_042_momentum", "alpha101_short"),
            ("alpha_harness_17", "alpha101_short"),
            ("cpcv_path_3", "cpcv"),
            ("news_gate_eurusd", "news_gate"),
            ("macro_event_nfp", "macro_event"),
            ("agent_rl_v1", "agent_based"),
            ("model_kalman_spread", "model_based"),
            ("sprt_breakout", "statistical_gate"),
            ("spx_volatility_skew", "volatility_derivatives"),
            ("meanrev_pairs_1", "mean_reversion"),
            ("momentum_tsmom_4h", "momentum_trend"),
            ("full_pipeline_eurusd_m15", "full_pipeline"),
            ("EURUSD_M15_backtest", "full_pipeline"),
        ],
    )
    def test_classify_derives_the_family(self, strategy_id, expected):
        assert classify(strategy_id) == expected

    def test_unattributable_identity_stays_unclassified(self):
        assert classify("some-random-thing") == UNCLASSIFIED
        assert classify("") == UNCLASSIFIED

    def test_pipeline_metadata_attributes_an_uninformative_id(self):
        """The continuous pipeline stamps symbol/timeframe on every run."""
        assert classify("run_7", {"symbol": "EURUSD", "timeframe": "M15"}) == "full_pipeline"
        # Half a stamp is not a claim.
        assert classify("run_7", {"symbol": "EURUSD"}) == UNCLASSIFIED


# ---------------------------------------------------------------------------
# 2. Resolution: explicit tag wins, "general" is not a claim
# ---------------------------------------------------------------------------
class TestResolution:
    def test_explicit_tag_wins_over_derivation(self):
        assert resolve_class("momentum_trend", "alpha_042_x") == "momentum_trend"

    def test_general_is_not_a_claim(self):
        """A stored ``general`` tag means "no class" — it must re-derive."""
        assert resolve_class(UNCLASSIFIED, "momentum_tsmom_4h") == "momentum_trend"
        assert scope_of(UNCLASSIFIED) is None
        assert scope_of(None) is None
        assert scope_of("") is None

    def test_unattributable_resolves_to_none(self):
        """None means GLOBAL — the strictest, fail-closed bar."""
        assert resolve_class(None, "zzz-unattributable") is None
        assert resolve_class(UNCLASSIFIED, "zzz-unattributable") is None

    def test_scope_of_returns_the_claim(self):
        assert scope_of("cpcv") == "cpcv"


# ---------------------------------------------------------------------------
# 3. The ledger: class pools are complete, and untagged rows are backfilled
# ---------------------------------------------------------------------------
class TestLedgerClassPools:
    def test_untagged_rows_are_derived_into_their_family(self, ledger):
        """Legacy rows (recorded before the taxonomy) must join their family.

        Otherwise the class pool holds a fraction of the family it claims to
        represent and the bar it produces is a fiction.
        """
        for i in range(5):
            ledger.record_trial(strategy_id=f"momentum_tsmom_{i}", sharpe=0.2,
                                n_observations=100)
        # Not tagged — the taxonomy derives the family at read time.
        assert ledger.n_trials_for_class("momentum_trend") == 5
        assert ledger.list_strategy_classes()["momentum_trend"] == 5

    def test_class_pool_counts_every_row_of_the_family(self, ledger):
        """Mixed tagged/untagged rows of one family form ONE pool."""
        ledger.record_trial(strategy_id="momentum_a", sharpe=0.1, n_observations=100,
                            strategy_class="momentum_trend")
        for i in range(4):
            ledger.record_trial(strategy_id=f"momentum_b{i}", sharpe=0.1,
                                n_observations=100)
        ledger.record_trial(strategy_id="news_x", sharpe=0.1, n_observations=100)
        assert ledger.n_trials_for_class("momentum_trend") == 5
        assert ledger.n_trials_for_class("news_gate") == 1
        assert ledger.n_trials_for_class(None) == ledger.n_trials == 6

    def test_explicit_tag_is_authoritative(self, ledger):
        """A caller's own claim outranks the name pattern."""
        ledger.record_trial(strategy_id="momentum_a", sharpe=0.1, n_observations=100,
                            strategy_class="my_programme")
        assert ledger.n_trials_for_class("my_programme") == 1
        assert ledger.n_trials_for_class("momentum_trend") == 0

    def test_group_by_effective_class_handles_json_metadata(self):
        rows = [
            # An uninformative id, but the pipeline stamped its cell.
            ("run_7", UNCLASSIFIED, 0.5, '{"symbol": "EURUSD", "timeframe": "M15"}'),
            ("news_a", UNCLASSIFIED, 0.2, None),
            ("custom", "custom_class", 0.1, None),
        ]
        groups = group_by_effective_class(rows)
        assert groups["full_pipeline"] == [0.5]
        assert groups["news_gate"] == [0.2]
        assert groups["custom_class"] == [0.1]

    def test_effective_class_keeps_stored_claims(self):
        assert effective_class("cpcv", "momentum_a") == "cpcv"
        assert effective_class(UNCLASSIFIED, "momentum_a") == "momentum_trend"
        assert effective_class(UNCLASSIFIED, "zzz") == UNCLASSIFIED


# ---------------------------------------------------------------------------
# 4. The gate: a class-scoped hypothesis faces a lower bar than the global one
# ---------------------------------------------------------------------------
class TestScopedGate:
    def test_same_sharpe_passes_scoped_and_fails_global(self, ledger):
        """The whole point: scope moves the bar left, for the same evidence."""
        # A crowded global family: 200 prior null trials.
        for i in range(200):
            ledger.record_trial(strategy_id=f"null_{i}", sharpe=0.0, n_observations=100)

        # Raw per-period estimator (~2.7 SE over 250 observations): above a
        # fresh class bar, below the crowded global bar.
        r_global = ledger.evaluate_dsr(
            estimated_sharpe=0.17, n_observations=250,
            strategy_id="zzz_unattributable", annualized=False,
        )
        r_scoped = ledger.evaluate_dsr(
            estimated_sharpe=0.17, n_observations=250,
            strategy_id="momentum_probe", annualized=False,
        )

        assert r_global.n_trials == 201          # global, fail-closed
        assert r_scoped.n_trials == 1            # its own family only
        assert r_global.is_significant is False
        assert r_scoped.is_significant is True
        assert r_scoped.max_expected_sharpe < r_global.max_expected_sharpe
        assert r_scoped.strategy_class == "momentum_trend"
        assert r_global.strategy_class == "global"

    def test_unattributable_hypothesis_keeps_the_global_bar(self, ledger):
        """Fail-closed: no family claim => the strictest bar."""
        for i in range(200):
            ledger.record_trial(strategy_id=f"null_{i}", sharpe=0.0, n_observations=100)
        r = ledger.evaluate_dsr(
            estimated_sharpe=0.17, n_observations=250,
            strategy_id="zzz_unattributable", annualized=False,
        )
        assert r.n_trials == 201
        assert r.strategy_class == "global"

    def test_explicit_general_tag_does_not_buy_a_small_pool(self, ledger):
        """``general`` is not a class — it must not deflate against itself."""
        for i in range(200):
            ledger.record_trial(strategy_id=f"null_{i}", sharpe=0.0, n_observations=100)
        r = ledger.evaluate_dsr(
            estimated_sharpe=0.17, n_observations=250,
            strategy_id="zzz_unattributable", strategy_class=UNCLASSIFIED,
            annualized=False,
        )
        assert r.n_trials == 201
        assert r.strategy_class == "global"

    def test_recorded_trial_joins_its_own_family(self, ledger):
        """The trial being gated is part of the family it is gated against."""
        ledger.evaluate_dsr(
            estimated_sharpe=0.5, n_observations=300,
            strategy_id="momentum_probe", annualized=False,
        )
        assert ledger.n_trials_for_class("momentum_trend") == 1
        assert ledger.n_trials_for_class(None) == 1


# ---------------------------------------------------------------------------
# 5. The calibration table: the bar moves LEFT once classes are used
# ---------------------------------------------------------------------------
class TestCalibrationTable:
    def _table(self, tmp_path, **kwargs):
        from research.trial_class_calibration import build_calibration_table

        db = str(tmp_path / "cal.db")
        led = TrialLedger(db_path=db)
        for i in range(300):
            led.record_trial(strategy_id=f"null_{i}", sharpe=0.0, n_observations=100)
        for i in range(20):
            led.record_trial(strategy_id=f"momentum_tsmom_{i}", sharpe=0.1,
                             n_observations=100)
        return build_calibration_table(db_path=db, measure_power=False, **kwargs)

    def test_global_row_is_the_crowded_family(self, tmp_path):
        table = self._table(tmp_path)
        rows = {row["class_id"]: row for row in table["rows"]}
        assert rows["global"]["n_trials"] == 320
        assert rows["momentum_trend"]["n_trials"] == 20

    def test_every_class_bar_sits_left_of_the_global_bar(self, tmp_path):
        table = self._table(tmp_path)
        rows = {row["class_id"]: row for row in table["rows"]}
        for class_id, row in rows.items():
            if class_id == "global":
                continue
            assert row["luck_bar_se"] < rows["global"]["luck_bar_se"]
            assert row["required_sharpe"] < rows["global"]["required_sharpe"]
            assert row["shift_vs_global"]["required_sharpe"] > 0

    def test_table_renders_and_reports_the_shift(self, tmp_path):
        from research.trial_class_calibration import render_markdown

        table = self._table(tmp_path)
        text = render_markdown(table)
        assert "`global`" in text
        assert "momentum_trend" in text
        assert "luck bar (SE)" in text

    def test_rendered_table_carries_every_measured_sharpe(self, tmp_path):
        """The doc quotes the SR 2.50 column, so the render must print it.

        The rendered table is the artifact's human-readable face: a column
        the artifact measures but the render drops is a number the doc
        cannot be checked against.
        """
        from research.trial_class_calibration import render_markdown

        table = self._table(tmp_path)
        text = render_markdown(table)
        for sr in table["reference"]["true_sharpes_annualized"]:
            assert f"power @ SR {sr:.2f}" in text

    def test_class_scoping_restores_power(self, tmp_path):
        """The measured payoff: the same edge is detected more often."""
        from research.trial_class_calibration import detection_rate

        global_power = detection_rate(300, 500, 2.5, reps=120, seed=7)
        class_power = detection_rate(20, 500, 2.5, reps=120, seed=7)
        assert class_power > global_power
