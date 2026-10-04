from pathlib import Path

from research.promotion_package import build, write, verify
from research.research_runs import record
from core.trading_safety import TradingStore


def _completed_ppo_report() -> dict:
    return {
        "status": "COMPLETED",
        "model_kind": "ppo_gae_position_mgmt_v1",
        "training_data_hash": "a" * 64,
        "artifact_sha256": "b" * 64,
        "mean_episode_reward": 0.1,
        "session_rollup": {"symbol": "EURUSD", "regime": "volatile", "episodes": 2},
        "execution_authority": False,
        "promotion_safe": False,
    }


def _completed_meta_report() -> dict:
    return {
        "status": "COMPLETED",
        "model_kind": "meta_rl_pearl_rl2_ppo_v1",
        "training_data_hash": "c" * 64,
        "artifact_sha256": "d" * 64,
        "mean_adapt_reward": -0.2,
        "task_rollup": [{"task": "EURUSD|range|smoke", "mean_adapt_reward": -0.2}],
        "execution_authority": False,
        "promotion_safe": False,
    }


def test_promotion_package_is_fail_closed_without_evidence(tmp_path: Path, monkeypatch):
    # build() audits the working directory; pin the result so this test does
    # not depend on whether the checkout happens to contain a finding.
    from research import promotion_package
    monkeypatch.setattr(promotion_package, "security_audit",
                        lambda *a, **k: {"status": "REVIEW_REQUIRED", "execution_authority": False})
    path = tmp_path / "state.db"
    TradingStore(str(path))
    package = build(str(path))

    assert package["status"] == "NOT_READY"
    assert package["execution_authority"] is False
    assert package["schema_version"] == "promotion-package-v1"
    assert len(package["package_hash"]) == 64
    assert "per-setup labelled sample/regime gate is not ready" in package["blockers"]
    assert "backtest-versus-demo evidence is missing or insufficient" in package["blockers"]
    assert "credential or trace security review is incomplete" in package["blockers"]
    assert "offline PPO+GAE research evidence is missing or insufficient" in package["blockers"]
    assert "offline meta-RL research evidence is missing or insufficient" in package["blockers"]
    assert "security" in package
    assert package["research_validation_runs"] == []
    assert package["ppo_research"]["execution_authority"] is False
    assert package["meta_rl_research"]["execution_authority"] is False


def test_promotion_package_carries_immutable_research_evidence(tmp_path: Path):
    path = tmp_path / "state-with-runs.db"
    TradingStore(str(path))
    rec = record(str(path), "baseline", {"status": "INSUFFICIENT_DATA", "dataset": {"sample_size": 0}})
    package = build(str(path))
    assert len(package["research_validation_runs"]) == 1
    assert package["research_validation_runs"][0]["run_id"] == rec["run_id"]
    assert package["research_validation_runs"][0]["execution_authority"] is False


def test_promotion_package_can_write_a_review_artifact(tmp_path: Path):
    path = tmp_path / "state.db"
    output = tmp_path / "promotion.json"
    TradingStore(str(path))
    package = write(str(path), output=str(output))

    assert package["output"] == str(output)
    assert output.exists()
    assert '"execution_authority": false' in output.read_text(encoding="utf-8").lower()
    assert '"package_hash"' in output.read_text(encoding="utf-8")
    checked = verify(str(output))
    assert checked["passed"] is True
    tampered = package.copy()
    tampered["status"] = "READY_FOR_OPERATOR_REVIEW"
    assert verify(tampered)["passed"] is False


def test_promotion_package_accepts_ppo_and_meta_rl_artifacts(tmp_path: Path):
    path = tmp_path / "state.db"
    TradingStore(str(path))
    package = build(
        str(path),
        ppo_report=_completed_ppo_report(),
        meta_rl_report=_completed_meta_report(),
    )
    assert package["execution_authority"] is False
    assert package["ppo_research"]["ready_for_review"] is True
    assert package["meta_rl_research"]["ready_for_review"] is True
    assert package["ppo_research"]["training_data_hash"] == "a" * 64
    assert package["meta_rl_research"]["artifact_sha256"] == "d" * 64
    assert "offline PPO+GAE research evidence is missing or insufficient" not in package["blockers"]
    assert "offline meta-RL research evidence is missing or insufficient" not in package["blockers"]
    # Other gates still fail closed.
    assert package["status"] == "NOT_READY"


def test_promotion_package_rejects_authority_claim_in_rl_evidence(tmp_path: Path):
    path = tmp_path / "state.db"
    TradingStore(str(path))
    bad = _completed_ppo_report()
    bad["execution_authority"] = True
    package = build(str(path), ppo_report=bad, meta_rl_report=_completed_meta_report())
    assert package["ppo_research"]["ready_for_review"] is False
    assert "offline PPO+GAE research evidence is missing or insufficient" in package["blockers"]
    assert package["execution_authority"] is False
