from research.strategy_accuracy import build_strategy_accuracy_scorecard
from research.research_validation import promotion_gate, _setup_floor_reasons
from operations.account_growth import AccountGrowthController


def test_setup_floor_reasons_expectancy_and_pf():
    weak = {
        "closed_trades": 120,
        "r_observations": 120,
        "purged_walk_forward_folds": 2,
        "expectancy_r": -0.1,
        "profit_factor": 0.8,
        "gross_wins_r": 10,
        "gross_losses_r": 12,
    }
    reasons = _setup_floor_reasons(weak, minimum_closed=100)
    assert any("expectancy_r" in r for r in reasons)
    assert any("profit_factor" in r for r in reasons)

    strong = {
        "closed_trades": 120,
        "r_observations": 120,
        "purged_walk_forward_folds": 2,
        "expectancy_r": 0.2,
        "profit_factor": 1.5,
        "gross_wins_r": 30,
        "gross_losses_r": 20,
        # Floors added with the P0 hardening (sharpe_ratio >= 0.5,
        # win_rate >= 0.4): the "strong" fixture must clear them too.
        "sharpe_ratio": 1.0,
        "win_rate": 0.55,
    }
    assert _setup_floor_reasons(strong, minimum_closed=100) == []


def test_promotion_gate_exposes_floors(tmp_path):
    report = promotion_gate(str(tmp_path / "empty.db"))
    assert report["allowed_to_influence_execution"] is False
    assert "floors" in report
    assert report["floors"]["min_profit_factor"] == 1.2
    assert report["eligible_setups"] == []
    assert "stress" in report


def test_strategy_accuracy_scorecard_empty(tmp_path):
    card = build_strategy_accuracy_scorecard(str(tmp_path / "empty.db"))
    assert card["execution_authority"] is False
    assert card["sample_size"] == 0
    assert card["scorecards"] == []


class _FakeStore:
    def __init__(self, path):
        self.path = path
        self.state = {}

    def set_state(self, key, value):
        self.state[key] = value

    def get_state(self, key, default=None):
        return self.state.get(key, default)


def test_account_growth_controller_never_raises_risk(tmp_path):
    store = _FakeStore(str(tmp_path / "g.db"))
    controller = AccountGrowthController(store, base_risk=0.01)
    advisory = controller.refresh(starting_equity=10_000)
    assert advisory["execution_authority"] is False
    assert controller.effective_risk_fraction(0.01) <= 0.01
    assert controller.effective_risk_fraction(0.005) <= 0.005
    # Even if advisory were somehow higher, clamp to base.
    store.state["account_growth_advisory"] = {
        "suggested_risk_per_trade": 0.05,
        "execution_authority": False,
    }
    controller._last = None
    assert controller.effective_risk_fraction(0.01) == 0.01
