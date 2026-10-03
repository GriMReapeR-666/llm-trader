"""Build a fail-closed validation package for research or rollout review.

The package gathers evidence from independent local gates but never grants
execution authority.  It makes the missing-evidence reasons explicit so a
reviewer cannot mistake a green unit test or a small sample for promotion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

try:
    from forward_test_report import compare as compare_forward_test
    from forward_test_report import MIN_FORWARD_SAMPLE
except ImportError:
    from research.forward_test_report import compare as compare_forward_test
    from research.forward_test_report import MIN_FORWARD_SAMPLE

try:
    from journal_audit import audit, qualified_metrics
except ImportError:
    from operations.journal_audit import audit, qualified_metrics

try:
    from live_rollout_gate import status as live_approval_status
except ImportError:
    from operations.live_rollout_gate import status as live_approval_status

try:
    from research_stress import stress_report
except ImportError:
    from research.research_stress import stress_report

try:
    from research_validation import promotion_gate
except ImportError:
    from research.research_validation import promotion_gate

try:
    from research_runs import list_runs as list_validation_runs
except ImportError:
    from research.research_runs import list_runs as list_validation_runs

try:
    from sample_gate import report as sample_report
except ImportError:
    from research.sample_gate import report as sample_report

try:
    from security_audit import audit as security_audit
except ImportError:
    from operations.security_audit import audit as security_audit


PACKAGE_SCHEMA_VERSION = "promotion-package-v1"

ReportInput = Union[str, Dict[str, Any], None]


def verify(package_or_path: Any) -> Dict[str, Any]:
    """Verify a promotion package hash without granting execution authority."""
    try:
        if isinstance(package_or_path, (str, bytes)):
            with open(package_or_path, "r", encoding="utf-8") as handle:
                package = json.load(handle)
        elif isinstance(package_or_path, dict):
            package = dict(package_or_path)
        else:
            raise TypeError("package must be a mapping or JSON path")
        expected = str(package.get("package_hash", ""))
        schema = package.get("schema_version")
        unsigned = dict(package)
        unsigned.pop("package_hash", None)
        canonical = json.dumps(unsigned, sort_keys=True, default=str, separators=(",", ":"))
        actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        passed = schema == PACKAGE_SCHEMA_VERSION and bool(expected) and expected == actual
        return {"passed": passed, "schema_version": schema,
                "expected_hash": expected, "actual_hash": actual,
                "execution_authority": False,
                "reason": "package hash and schema verified" if passed else "package hash or schema mismatch"}
    except Exception as exc:
        return {"passed": False, "execution_authority": False,
                "reason": "package verification failed", "error": str(exc)[:500]}


def _load_json_mapping(value: ReportInput) -> Dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    path = Path(str(value))
    if not path.exists():
        return {"_load_error": f"missing file: {path}"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface as evidence failure
        return {"_load_error": str(exc)[:500]}
    if not isinstance(payload, dict):
        return {"_load_error": "artifact must be a JSON object"}
    return payload


def summarize_offline_rl_evidence(
    report_or_path: ReportInput,
    *,
    kind: str,
    expected_model_prefix: str,
) -> Dict[str, Any]:
    """Normalize PPO / Meta-RL research reports into promotion evidence.

    Never grants authority. A COMPLETED report still remains ``promotion_safe=False``.
    """
    raw = _load_json_mapping(report_or_path)
    base: Dict[str, Any] = {
        "kind": kind,
        "status": "NOT_PROVIDED",
        "ready_for_review": False,
        "execution_authority": False,
        "promotion_safe": False,
        "training_data_hash": None,
        "artifact_sha256": None,
        "model_kind": None,
        "metrics": {},
        "reason": f"{kind} research evidence was not supplied.",
    }
    if not raw:
        return base
    if raw.get("_load_error"):
        base["status"] = "INVALID"
        base["reason"] = f"{kind} evidence could not be loaded: {raw['_load_error']}"
        return base

    # Weight artifacts nest metrics under meta; training reports are flat.
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    status = str(raw.get("status") or meta.get("report_status") or "UNKNOWN")
    training_hash = str(
        raw.get("training_data_hash")
        or meta.get("training_data_hash")
        or ""
    ).strip() or None
    model_kind = str(raw.get("model_kind") or "").strip() or None
    artifact_sha = str(raw.get("artifact_sha256") or "").strip() or None
    if not artifact_sha and raw.get("artifact_path"):
        try:
            artifact_sha = hashlib.sha256(Path(str(raw["artifact_path"])).read_bytes()).hexdigest()
        except Exception:  # noqa: BLE001
            artifact_sha = None

    claimed_authority = bool(raw.get("execution_authority", False))
    claimed_promo = bool(raw.get("promotion_safe", False))

    metrics: Dict[str, Any] = {}
    if "mean_episode_reward" in raw:
        metrics["mean_episode_reward"] = raw.get("mean_episode_reward")
    if "mean_adapt_reward" in raw:
        metrics["mean_adapt_reward"] = raw.get("mean_adapt_reward")
    if isinstance(raw.get("session_rollup"), dict):
        metrics["session_rollup"] = raw["session_rollup"]
    if isinstance(raw.get("task_rollup"), list):
        metrics["task_count"] = len(raw["task_rollup"])
        metrics["task_rollup"] = raw["task_rollup"]
    if isinstance(raw.get("updates"), list):
        metrics["n_updates"] = len(raw["updates"])
    if isinstance(raw.get("configuration"), dict):
        metrics["configuration"] = {
            k: raw["configuration"].get(k)
            for k in ("simulator", "seed", "symbol", "regime", "tasks", "backend", "model_kind")
            if k in raw["configuration"]
        }

    evidence = {
        "kind": kind,
        "status": status,
        "ready_for_review": False,
        "execution_authority": False,
        "promotion_safe": False,
        "training_data_hash": training_hash,
        "artifact_sha256": artifact_sha,
        "model_kind": model_kind,
        "metrics": metrics,
        "source_status": status,
        "claimed_execution_authority": claimed_authority,
        "claimed_promotion_safe": claimed_promo,
        "reason": "",
    }

    reasons = []
    if claimed_authority:
        reasons.append(f"{kind} artifact illegally claimed execution_authority")
    if claimed_promo:
        reasons.append(f"{kind} artifact illegally claimed promotion_safe")
    if status != "COMPLETED":
        # Weight-only artifacts may omit status; accept when model_kind + hash present.
        if not (model_kind and training_hash and str(model_kind).startswith(expected_model_prefix)):
            reasons.append(f"{kind} status is not COMPLETED")
    if not training_hash or len(training_hash) != 64:
        reasons.append(f"{kind} training_data_hash missing or not SHA-256")
    else:
        try:
            int(training_hash, 16)
        except ValueError:
            reasons.append(f"{kind} training_data_hash is not hex SHA-256")
    if model_kind and not str(model_kind).startswith(expected_model_prefix):
        reasons.append(f"{kind} model_kind mismatch (expected prefix {expected_model_prefix})")
    if not model_kind:
        reasons.append(f"{kind} model_kind missing")

    if reasons:
        evidence["status"] = "INSUFFICIENT" if status == "COMPLETED" else status
        evidence["reason"] = "; ".join(reasons)
        evidence["ready_for_review"] = False
    else:
        evidence["status"] = "READY_FOR_RESEARCH_REVIEW"
        evidence["ready_for_review"] = True
        evidence["reason"] = (
            f"{kind} offline evidence recorded for human review; never authorizes orders."
        )
    return evidence


def build(
    path: str = "trading_state.db",
    *,
    backtest_report: str = "",
    minimum_demo_sample: int = MIN_FORWARD_SAMPLE,
    ppo_report: ReportInput = None,
    meta_rl_report: ReportInput = None,
) -> Dict[str, Any]:
    integrity = audit(path)
    samples = sample_report(path)
    research = promotion_gate(path)
    validation_runs = list_validation_runs(path, limit=50)
    stress = stress_report(path)
    metrics = qualified_metrics(path)
    approval = live_approval_status(path)
    security = security_audit()
    forward: Dict[str, Any]
    if backtest_report:
        forward = compare_forward_test(backtest_report, path, minimum_demo_sample=minimum_demo_sample)
    else:
        forward = {"status": "NOT_PROVIDED", "execution_authority": False,
                   "reason": "A cost-aware backtest report was not supplied."}

    ppo_evidence = summarize_offline_rl_evidence(
        ppo_report, kind="ppo", expected_model_prefix="ppo_"
    )
    meta_evidence = summarize_offline_rl_evidence(
        meta_rl_report, kind="meta_rl", expected_model_prefix="meta_rl_"
    )

    blockers = []
    if not integrity.get("passed", False):
        blockers.append("journal integrity failed")
    if samples.get("status") != "READY_FOR_RESEARCH":
        blockers.append("per-setup labelled sample/regime gate is not ready")
    if not research.get("eligible_setups"):
        blockers.append("no setup has sufficient purged walk-forward research evidence")
    if stress.get("status") != "READY_FOR_REVIEW":
        blockers.append("cost/sequence stress evidence is insufficient")
    if forward.get("status") != "READY_FOR_OPERATOR_REVIEW":
        blockers.append("backtest-versus-demo evidence is missing or insufficient")
    if not approval.get("active", False):
        blockers.append("explicit tiny-live operator approval is absent or expired")
    if security.get("status") != "CLEAR_FOR_OPERATOR_REVIEW":
        blockers.append("credential or trace security review is incomplete")
    if not ppo_evidence.get("ready_for_review"):
        blockers.append("offline PPO+GAE research evidence is missing or insufficient")
    if not meta_evidence.get("ready_for_review"):
        blockers.append("offline meta-RL research evidence is missing or insufficient")

    package = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "journal_path": path,
        "status": "READY_FOR_OPERATOR_REVIEW" if not blockers else "NOT_READY",
        "execution_authority": False,
        "blockers": blockers,
        "integrity": integrity,
        "qualified_metrics": metrics,
        "sample_gate": samples,
        "research_gate": research,
        "research_validation_runs": validation_runs,
        "stress": stress,
        "forward_test": forward,
        "live_approval": approval,
        "security": security,
        "ppo_research": ppo_evidence,
        "meta_rl_research": meta_evidence,
        "limitation": "This package is evidence for human review; it never authorizes orders or bypasses deterministic risk gates.",
    }
    canonical = json.dumps(package, sort_keys=True, default=str, separators=(",", ":"))
    package["package_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return package


def write(
    path: str = "trading_state.db",
    *,
    backtest_report: str = "",
    minimum_demo_sample: int = MIN_FORWARD_SAMPLE,
    output: Optional[str] = None,
    ppo_report: ReportInput = None,
    meta_rl_report: ReportInput = None,
) -> Dict[str, Any]:
    package = build(
        path,
        backtest_report=backtest_report,
        minimum_demo_sample=minimum_demo_sample,
        ppo_report=ppo_report,
        meta_rl_report=meta_rl_report,
    )
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(package, handle, indent=2, default=str)
        package["output"] = output
    return package
