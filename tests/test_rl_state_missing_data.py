from research.rl_trainer import RLTrainer


def test_rl_state_key_preserves_unknown_missing_features():
    trainer = RLTrainer(model_path="__missing_rl_model_for_test__.json")
    key = trainer._build_state_key({
        "confluence_score": None,
        "news_sentiment": None,
        "trend_strength": None,
    })
    assert "conf:unknown" in key
    assert "news:unknown" in key
    assert "trend:unknown" in key
    assert key != "default_state"


def test_rl_state_key_remains_numeric_for_complete_snapshot():
    trainer = RLTrainer(model_path="__missing_rl_model_for_test__.json")
    key = trainer._build_state_key({
        "confluence_score": 4,
        "news_sentiment": -0.5,
        "trend_strength": 0.8,
    })
    assert "conf:bin_1" in key
    assert "news:bin_0" in key
    assert "trend:bin_2" in key
