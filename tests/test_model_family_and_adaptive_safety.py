import math

import pytest

from research.adaptive_models import OnlineGarch, OnlineHMM, fractional_bayesian_kelly
from research.model_family_gate import evaluation_plan


def test_model_family_plan_rejects_weak_configuration(tmp_path):
    result = evaluation_plan(str(tmp_path / "empty.db"), minimum_train=10)
    assert result["status"] == "INVALID_INPUT"
    assert result["execution_authority"] is False


def test_adaptive_models_reject_nonfinite_observations():
    with pytest.raises(ValueError):
        OnlineGarch(variance=math.nan)
    garch = OnlineGarch()
    with pytest.raises(ValueError):
        garch.update(math.nan)
    hmm = OnlineHMM()
    with pytest.raises(ValueError):
        hmm.update(float("inf"))
    kelly = fractional_bayesian_kelly(1, 1, 1.0, 1.0, fraction=math.nan)
    assert kelly["eligible"] is False
    assert kelly["execution_authority"] is False
