"""Regression: WebDashboardAdapter.update_rl_stats against the real RLTrainer.

Crash being guarded against (mt5_trader paper loop):
    AttributeError: 'RLTrainer' object has no attribute 'rewards_history'
The adapter once hard-read `trainer.rewards_history` / `trainer.epsilon_history`;
RLTrainer exposes `episode_rewards` and a scalar `epsilon` instead.

Second regression guarded here: epsilon_history used to be a stub returning
``[self.epsilon]``. zipped against the reward series that collapsed the whole
learning curve to one point, and padding it out to full length would have drawn
a flat exploration curve over a real decay. The series must now be
index-aligned with None for episodes whose epsilon was never recorded.
"""
import pytest

from research.rl_trainer import RLTrainer
from ui.web_dashboard_adapter import WebDashboardAdapter


@pytest.fixture
def adapter():
    return WebDashboardAdapter(api_url=None)


def test_update_rl_stats_with_real_trainer_does_not_crash(adapter, tmp_path):
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))

    adapter.update_rl_stats(trainer)  # must not raise

    assert adapter.state.rl_metrics["episodes"] == []
    assert adapter.state.rl_metrics["rewards"] == []
    # No episodes recorded -> no per-episode epsilon history. The live scalar is
    # exposed on its own key, not smuggled in as a one-point "history".
    assert adapter.state.rl_metrics["epsilon"] == []
    assert adapter.state.rl_metrics["epsilon_current"] == pytest.approx(0.2)


def test_update_rl_stats_populates_series_after_episodes(adapter, tmp_path):
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    trainer.episode_rewards = [1.0, -0.5, 2.0]
    trainer.episode_epsilons = [0.2, 0.2, 0.2]

    adapter.update_rl_stats(trainer)

    assert adapter.state.rl_metrics["episodes"] == [0, 1, 2]
    assert adapter.state.rl_metrics["rewards"] == [1.0, -0.5, 2.0]
    assert adapter.state.rl_metrics["epsilon"] == [0.2, 0.2, 0.2]


def test_learning_curve_is_not_truncated_by_short_epsilon(adapter, tmp_path):
    """/api/brain/metrics zips episodes+rewards+epsilon.

    A length-1 epsilon series silently collapsed the whole learning curve to a
    single point, hiding the entire reward history from the dashboard.
    """
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    trainer.episode_rewards = [1.0, -0.5, 2.0, 3.0, -1.0]
    trainer.episode_epsilons = [0.2, 0.2, 0.2, 0.2, 0.2]

    adapter.update_rl_stats(trainer)

    episodes = adapter.state.rl_metrics["episodes"]
    rewards = adapter.state.rl_metrics["rewards"]
    epsilon = adapter.state.rl_metrics["epsilon"]
    assert len(episodes) == len(rewards) == len(epsilon) == 5


def test_missing_epsilon_is_padded_with_none_not_fabricated(adapter, tmp_path):
    """No fake: an unrecorded epsilon must stay unknown, not borrow the scalar."""
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    trainer.episode_rewards = [1.0, -0.5, 2.0, 3.0]
    trainer.episode_epsilons = [0.2, 0.2]  # only the first two were recorded
    trainer.epsilon = 0.05  # current value is LOWER than either recorded one

    adapter.update_rl_stats(trainer)

    epsilon = adapter.state.rl_metrics["epsilon"]
    assert len(epsilon) == 4
    assert epsilon[:2] == [0.2, 0.2]
    assert epsilon[2:] == [None, None]
    # The real current value is still available, just not as history.
    assert adapter.state.rl_metrics["epsilon_current"] == pytest.approx(0.05)


def test_no_epsilon_history_at_all_yields_no_fabricated_series(adapter, tmp_path):
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    trainer.episode_rewards = [1.0, -0.5, 2.0]
    trainer.episode_epsilons = []

    adapter.update_rl_stats(trainer)

    assert adapter.state.rl_metrics["epsilon"] == []
    assert adapter.state.rl_metrics["epsilon_current"] == pytest.approx(0.2)


def test_update_rl_stats_with_none_is_noop(adapter):
    adapter.update_rl_stats(None)  # must not raise


def test_rl_trainer_backward_compatibility_properties(tmp_path):
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    trainer.episode_rewards = [1.0, -0.5, 2.0]
    trainer.episode_epsilons = [0.2, 0.2, 0.2]

    # Verify backward-compatibility aliases
    assert trainer.rewards_history == [1.0, -0.5, 2.0]
    assert trainer.epsilon_history == [0.2, 0.2, 0.2]


def test_untrained_trainer_reports_no_epsilon_history(tmp_path):
    """An untrained trainer has no trace. Returning [epsilon] would be a fake."""
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    assert trainer.epsilon_history == []


def test_trained_hyperparameters_survive_a_restart(tmp_path):
    """save_model writes hyperparameters; load_model must not drop them.

    Otherwise a trained model silently resumes at the constructor defaults,
    discarding the learned epsilon decay and contradicting the recorded trace.
    """
    path = tmp_path / "rl.json"
    t = RLTrainer(model_path=str(path))
    for _ in range(4):
        t.decay_epsilon()
    t.episode_rewards = [1.0, 1.0, 1.0, 1.0]
    t.episode_epsilons = [0.2, 0.199, 0.198, 0.197]
    t.save_model(str(path))

    r = RLTrainer(model_path=str(path))
    r.load_model(str(path))

    assert r.epsilon == pytest.approx(t.epsilon)
    assert r.learning_rate == pytest.approx(t.learning_rate)
    assert r.discount_factor == pytest.approx(t.discount_factor)
    assert r.epsilon_history == [pytest.approx(e) for e in t.episode_epsilons]


def test_checkpoint_without_epsilon_trace_is_not_backfilled(tmp_path):
    """No fake: a checkpoint predating the trace loads an empty trace."""
    import json

    path = tmp_path / "rl.json"
    t = RLTrainer(model_path=str(path))
    t.episode_rewards = [1.0, 1.0, 1.0]
    t.save_model(str(path))

    data = json.loads(path.read_text())
    data.pop("episode_epsilons", None)
    path.write_text(json.dumps(data))

    r = RLTrainer(model_path=str(path))
    r.load_model(str(path))

    assert r.episode_rewards == [1.0, 1.0, 1.0]
    assert r.epsilon_history == []


def test_epsilon_trace_records_the_rate_that_governed_each_episode(tmp_path):
    """Real per-episode capture: pre-decay epsilon, index-aligned with rewards."""
    trainer = RLTrainer(model_path=str(tmp_path / "rl_model.json"))
    for _ in range(3):
        trainer.decay_epsilon()

    assert trainer.epsilon < 0.2
    trainer.episode_rewards = [1.0, 1.0, 1.0]
    trainer.episode_epsilons = [float(trainer.epsilon)] * 3

    assert trainer.epsilon_history == [pytest.approx(trainer.epsilon)] * 3
    assert len(trainer.epsilon_history) == len(trainer.episode_rewards)
