"""Persistent, explicit tiny-live rollout approval gate.

The gate is intentionally separate from ``LLM_TRADER_ALLOW_LIVE``.  An
environment flag says that the operator permits the application to consider
live mode; this record says the operator reviewed current evidence, selected a
small risk cap, and approved a bounded expiry.  It never bypasses the normal
validator, portfolio arbiter, kill switch, broker policy, or reconciliation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import hashlib
import os
from typing import Any, Dict, Iterable, Optional

from core.trading_safety import TradingStore


CONFIRMATION = "APPROVE_TINY_LIVE"
STATE_KEY = "live_rollout_approval"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _evidence_manifest(evidence: Iterable[str]) -> list[Dict[str, Any]]:
    manifest = []
    for item in sorted({str(value).strip() for value in evidence if str(value).strip()}):
        path = os.path.abspath(item)
        if os.path.isfile(path):
            digest = hashlib.sha256()
            size = 0
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            manifest.append({"reference": item, "kind": "FILE", "path": path,
                             "sha256": digest.hexdigest(), "size": size})
        else:
            manifest.append({"reference": item, "kind": "REFERENCE", "path": path,
                             "sha256": None, "size": None})
    return manifest


def _manifest_hash(manifest: list[Dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def _verify_manifest(manifest: list[Dict[str, Any]], expected_hash: str) -> Dict[str, Any]:
    """Validate the approval manifest without changing the stored record.

    File evidence is intentionally re-hashed on every gate read.  This makes
    an approval fail closed immediately if a report is edited or removed after
    approval; a manual ``verify`` command is useful for diagnostics but is not
    required to revoke execution authority.
    """
    if not isinstance(manifest, list):
        return {"manifest_hash_valid": False, "evidence_valid": False,
                "checks": [{"status": "MALFORMED", "valid": False}],
                "reason": "approval evidence manifest is malformed"}
    checks = []
    for item in manifest:
        if not isinstance(item, dict):
            checks.append({"status": "MALFORMED", "valid": False})
            continue
        if item.get("kind") != "FILE":
            checks.append({"reference": item.get("reference"), "status": "REFERENCE_ONLY", "valid": True})
            continue
        file_path = item.get("path") or item.get("reference")
        if not os.path.isfile(file_path):
            checks.append({"reference": item.get("reference"), "status": "MISSING", "valid": False})
            continue
        digest = hashlib.sha256()
        size = 0
        with open(file_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        valid = digest.hexdigest() == item.get("sha256") and size == item.get("size")
        checks.append({"reference": item.get("reference"), "status": "MATCH" if valid else "DRIFT",
                       "valid": valid, "sha256": digest.hexdigest(), "size": size})
    manifest_hash_valid = bool(expected_hash) and expected_hash == _manifest_hash(manifest)
    evidence_valid = bool(manifest) and manifest_hash_valid and all(item["valid"] for item in checks)
    return {"manifest_hash_valid": manifest_hash_valid, "evidence_valid": evidence_valid, "checks": checks}


def status(path: str = "trading_state.db") -> Dict[str, Any]:
    store = TradingStore(path)
    raw_record = store.get_state(STATE_KEY, {})
    record = raw_record if isinstance(raw_record, dict) else {
        "approved": False,
        "invalidation_reason": "stored rollout approval is malformed",
    }
    expires = _parse(record.get("expires_at"))
    manifest = record.get("evidence_manifest")
    if manifest is None:
        manifest = []
    evidence = _verify_manifest(manifest, record.get("evidence_hash", ""))
    if record.get("approved", False) and not evidence["evidence_valid"]:
        # Do not allow a transient edit/delete (or later restoration) to
        # silently reactivate a previously approved rollout.  Re-approval is
        # deliberately required after any evidence integrity failure.
        record = {
            **record,
            "approved": False,
            "invalidated_at": _now().isoformat(),
            "invalidation_reason": "approval evidence missing, drifted, or has an invalid manifest",
        }
        store.set_state(STATE_KEY, record)
    active = bool(record.get("approved", False) and expires and expires > _now() and evidence["evidence_valid"])
    return {
        "active": active,
        "approved": bool(record.get("approved", False)),
        "operator": record.get("operator", ""),
        "approved_at": record.get("approved_at"),
        "invalidated_at": record.get("invalidated_at"),
        "invalidation_reason": record.get("invalidation_reason", ""),
        "expires_at": record.get("expires_at"),
        "risk_fraction_cap": record.get("risk_fraction_cap"),
        "evidence_hash": record.get("evidence_hash", ""),
        "evidence_manifest": manifest,
        "evidence_hash_valid": evidence["manifest_hash_valid"],
        "evidence_valid": evidence["evidence_valid"],
        "evidence_checks": evidence["checks"],
        "note": record.get("note", ""),
        # Approval is evidence for the separate runtime gate; it is never
        # itself an order permission or execution authority grant.
        "execution_authority": False,
        "limitation": "Approval is only a rollout prerequisite; all deterministic gates and broker reconciliation still apply.",
    }


def approve(path: str = "trading_state.db", *, operator: str, note: str,
            evidence: Iterable[str], risk_fraction_cap: float = 0.001,
            expires_hours: float = 24.0, confirmation: str = "") -> Dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise ValueError(f"explicit confirmation token required: {CONFIRMATION}")
    if not str(operator).strip() or not str(note).strip():
        raise ValueError("operator and evidence review note are required")
    evidence_items = sorted({str(item).strip() for item in evidence if str(item).strip()})
    if not evidence_items:
        raise ValueError("at least one evidence reference is required")
    if not 0 < float(risk_fraction_cap) <= 0.001:
        raise ValueError("tiny-live risk cap must be >0 and <=0.001 (0.1%)")
    if not 0 < float(expires_hours) <= 168:
        raise ValueError("approval expiry must be between 0 and 168 hours")
    approved_at = _now()
    expires_at = approved_at + timedelta(hours=float(expires_hours))
    evidence_manifest = _evidence_manifest(evidence_items)
    if not any(item.get("kind") == "FILE" for item in evidence_manifest):
        raise ValueError("at least one existing file evidence artifact is required")
    evidence_hash = _manifest_hash(evidence_manifest)
    record = {
        "approved": True, "operator": str(operator).strip()[:200],
        "approved_at": approved_at.isoformat(), "expires_at": expires_at.isoformat(),
        "risk_fraction_cap": float(risk_fraction_cap), "evidence_hash": evidence_hash,
        "evidence_count": len(evidence_manifest), "evidence_manifest": evidence_manifest,
        "note": str(note).strip()[:2000],
    }
    TradingStore(path).set_state(STATE_KEY, record)
    return {**record, "active": True, "execution_authority": False,
            "evidence_values_not_persisted": True,
            "limitation": "Approval does not bypass validator, arbiter, kill switch, broker policy, or reconciliation."}


def revoke(path: str = "trading_state.db", *, operator: str, note: str = "") -> Dict[str, Any]:
    if not str(operator).strip():
        raise ValueError("operator is required")
    record = {"approved": False, "revoked_at": _now().isoformat(),
              "revoked_by": str(operator).strip()[:200], "note": str(note).strip()[:2000]}
    TradingStore(path).set_state(STATE_KEY, record)
    return {**record, "active": False, "execution_authority": False}


def verify_evidence(path: str = "trading_state.db") -> Dict[str, Any]:
    """Re-hash file-backed approval evidence and report drift."""
    current = status(path)
    return {"active": current.get("active", False), "evidence_hash_valid": current.get("evidence_hash_valid", False),
            "evidence_valid": current.get("evidence_valid", False),
            "checks": current.get("evidence_checks", []), "execution_authority": False,
            "limitation": "Evidence verification is advisory and never grants order permission."}


def require(path: str = "trading_state.db", *, risk_fraction: Optional[float] = None) -> Dict[str, Any]:
    current = status(path)
    if not current["active"]:
        raise RuntimeError("live rollout approval is absent or expired; run journal live-approval approve after evidence review")
    cap = float(current.get("risk_fraction_cap") or 0)
    if risk_fraction is not None and float(risk_fraction) > cap + 1e-12:
        raise RuntimeError(f"requested live risk fraction {float(risk_fraction):.6f} exceeds approved tiny-live cap {cap:.6f}")
    return current
