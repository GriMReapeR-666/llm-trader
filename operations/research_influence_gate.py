"""Fail-closed gate for learned/adaptive influence on non-paper trading.

Research models may be calculated and displayed during paper research, but
their output must not affect a broker-backed proposal until the authoritative
journal has a sufficiently large labelled sample, qualifying research floors,
and an explicit, expiring operator approval. This module only records and
reports evidence; it never grants order permission.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Dict

try:
    from research.research_validation import promotion_gate
except ImportError:
    from research.research_validation import promotion_gate

try:
    from research.sample_gate import report as sample_gate_report
except ImportError:
    from research.sample_gate import report as sample_gate_report

try:
    from core.trading_safety import TradingStore
except ImportError:
    from core.trading_safety import TradingStore


STATE_KEY = "research_influence_approval"
MIN_SAMPLE = 100
_HASH = re.compile(r"^[0-9a-fA-F]{64}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _state(store: TradingStore) -> Dict[str, Any]:
    raw = store.get_state(STATE_KEY, {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _evidence(path: str) -> Dict[str, Any]:
    sample = sample_gate_report(path)
    research = promotion_gate(path)
    return {
        "sample": sample,
        "research": research,
        "sample_size": int(sample.get("sample_size", 0) or 0),
        "sample_ready": sample.get("status") == "READY_FOR_RESEARCH",
        "research_ready": bool(research.get("eligible_setups")),
    }


def _digest(evidence: Dict[str, Any]) -> str:
    material = {
        "sample_size": evidence.get("sample_size", 0),
        "sample_ready": bool(evidence.get("sample_ready")),
        "research_ready": bool(evidence.get("research_ready")),
        "eligible_setups": evidence.get("research", {}).get("eligible_setups", []),
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def status(path: str = "trading_state.db", *, model_kind: str = "all") -> Dict[str, Any]:
    store = TradingStore(path)
    approval = _state(store)
    try:
        evidence = _evidence(path)
        evidence_error = ""
    except Exception as exc:
        evidence = {"sample": {}, "research": {}, "sample_size": 0,
                    "sample_ready": False, "research_ready": False}
        evidence_error = str(exc)[:300]
    reasons = []
    if evidence_error:
        reasons.append(f"research evidence evaluation failed closed: {evidence_error}")
    if str(approval.get("status", "")).upper() != "APPROVED":
        reasons.append("explicit operator research-influence approval is absent")
    if str(approval.get("model_kind", "all")) not in {"all", str(model_kind)}:
        reasons.append("approval model scope does not match requested model")
    approval_minimum = _safe_int(approval.get("minimum_sample", 0))
    if approval_minimum < MIN_SAMPLE:
        reasons.append(f"approval minimum sample must be at least {MIN_SAMPLE}")
    if evidence["sample_size"] < max(MIN_SAMPLE, approval_minimum) or not evidence["sample_ready"]:
        reasons.append("current journal sample gate is not ready for the approved minimum")
    if not evidence["research_ready"]:
        reasons.append("current purged/OOS research promotion gate is not ready")
    if approval.get("oos_validated") is not True:
        reasons.append("approval does not record explicit out-of-sample validation")
    evidence_hash = str(approval.get("evidence_hash", ""))
    if not _HASH.fullmatch(evidence_hash):
        reasons.append("approval evidence hash is missing or malformed")
    elif not evidence_error and evidence_hash.lower() != _digest(evidence):
        reasons.append("approval evidence hash no longer matches current journal/promotion evidence")
    expires = _parse(approval.get("expires_at"))
    if expires is None or expires <= _now():
        reasons.append("approval is missing, malformed, or expired")
    allowed = not reasons
    return {
        "status": "APPROVED" if allowed else "BLOCKED",
        "allowed": allowed,
        "model_kind": str(model_kind),
        "approval": approval,
        "evidence": {
            "sample_size": evidence["sample_size"],
            "sample_ready": evidence["sample_ready"],
            "research_ready": evidence["research_ready"],
        },
        "reasons": reasons,
        "execution_authority": False,
    }


def allows(path: str, model_kind: str) -> bool:
    try:
        return bool(status(path, model_kind=model_kind).get("allowed"))
    except Exception:
        return False


def approve(path: str, *, model_kind: str = "all", operator: str = "",
            evidence_hash: str = "", note: str = "", expires_hours: float = 24.0,
            oos_validated: bool = False, minimum_sample: int = MIN_SAMPLE) -> Dict[str, Any]:
    operator = str(operator or "").strip()
    evidence_hash = str(evidence_hash or "").strip().lower()
    if not operator:
        raise ValueError("operator is required")
    if not _HASH.fullmatch(evidence_hash):
        raise ValueError("evidence_hash must be a SHA-256 hex digest")
    if oos_validated is not True:
        raise ValueError("explicit oos_validated=true is required")
    if isinstance(minimum_sample, bool) or not isinstance(minimum_sample, int) or minimum_sample < MIN_SAMPLE:
        raise ValueError(f"minimum_sample must be an integer >= {MIN_SAMPLE}")
    try:
        hours = float(expires_hours)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("expires_hours must be finite and positive") from None
    if hours <= 0 or hours > 24 * 31:
        raise ValueError("expires_hours must be in (0, 744]")
    try:
        evidence = _evidence(path)
    except Exception as exc:
        return {"status": "BLOCKED", "reason": f"research evidence evaluation failed closed: {str(exc)[:300]}",
                "execution_authority": False}
    if evidence["sample_size"] < minimum_sample or not evidence["sample_ready"]:
        return {"status": "BLOCKED", "reason": "current sample gate is not ready",
                "evidence": evidence["sample_size"], "execution_authority": False}
    if not evidence["research_ready"]:
        return {"status": "BLOCKED", "reason": "current OOS/promotion gate is not ready",
                "execution_authority": False}
    if evidence_hash != _digest(evidence):
        return {"status": "BLOCKED", "reason": "evidence hash does not match current journal/promotion evidence",
                "expected_evidence_hash": _digest(evidence), "execution_authority": False}
    now = _now()
    approval = {
        "status": "APPROVED", "model_kind": str(model_kind or "all"),
        "operator": operator[:120], "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=hours)).isoformat(),
        "minimum_sample": minimum_sample, "oos_validated": True,
        "evidence_hash": evidence_hash, "note": str(note or "")[:1000],
        "execution_authority": False,
    }
    store = TradingStore(path)
    store.set_state(STATE_KEY, approval)
    return {"status": "APPROVED", "approval": approval,
            "execution_authority": False,
            "limitation": "Approval only permits advisory model influence; deterministic gates still own orders."}


def revoke(path: str = "trading_state.db", *, operator: str = "", note: str = "") -> Dict[str, Any]:
    operator = str(operator or "").strip()
    if not operator:
        raise ValueError("operator is required")
    store = TradingStore(path)
    current = _state(store)
    current.update({"status": "REVOKED", "revoked_at": _now().isoformat(),
                    "revoked_by": operator[:120], "revoke_note": str(note or "")[:1000],
                    "execution_authority": False})
    store.set_state(STATE_KEY, current)
    return {"status": "REVOKED", "approval": current, "execution_authority": False}


def evidence_fingerprint(path: str = "trading_state.db") -> str:
    """Return a deterministic digest of the current gate evidence for review."""
    evidence = _evidence(path)
    return _digest(evidence)
