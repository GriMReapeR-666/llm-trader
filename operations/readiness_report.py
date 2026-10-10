"""Fail-closed operational readiness summary.

This is an operator view, not an execution permission.  It combines the
independent evidence gates already used by the trader and preserves each
source report so a blocked result can be audited without guessing.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

try:
    from core.broker_preflight import validate_envelope
except ImportError:
    from core.broker_preflight import validate_envelope

try:
    from operations.journal_audit import audit as audit_journal
except ImportError:
    from operations.journal_audit import audit as audit_journal

try:
    from operations.legacy_reconciliation import report as legacy_report
except ImportError:
    from operations.legacy_reconciliation import report as legacy_report

try:
    from operations.live_rollout_gate import status as live_rollout_status
except ImportError:
    from operations.live_rollout_gate import status as live_rollout_status

try:
    from operations.mt5_auth_diagnostics import report as auth_report
except ImportError:
    from operations.mt5_auth_diagnostics import report as auth_report

try:
    from research.sample_gate import report as sample_report
except ImportError:
    from research.sample_gate import report as sample_report

try:
    from operations.security_audit import audit as security_audit
except ImportError:
    from operations.security_audit import audit as security_audit

try:
    from operations.research_influence_gate import status as research_influence_status
except ImportError:
    from operations.research_influence_gate import status as research_influence_status

try:
    from core.trial_ledger import verify_chain_readonly as _verify_chain_main
except ImportError:
    from core.trial_ledger import verify_chain_readonly as _verify_chain_main

try:
    from core.strategy_trial_ledger import verify_chain_readonly as _verify_chain_cpcv
except ImportError:
    try:
        from core.strategy_trial_ledger import verify_chain_readonly as _verify_chain_cpcv
    except ImportError as _cpcv_err:
        def _verify_chain_cpcv():
            return {
                "valid": False,
                "status": "MODULE_IMPORT_ERROR",
                "error": f"core.strategy_trial_ledger could not be imported: {_cpcv_err}",
            }



def _redact_preflight_envelope(envelope: Any) -> Dict[str, Any]:
    """Return broker evidence safe for operator/readiness output.

    The journal may retain the authenticated identity internally for scope
    binding, but diagnostic JSON must not expose account login identifiers.
    """
    if not isinstance(envelope, dict):
        return {"status": "MALFORMED", "execution_authority": False}
    safe = dict(envelope)
    report = envelope.get("report")
    if isinstance(report, dict):
        safe_report = dict(report)
        account = report.get("account")
        if isinstance(account, dict):
            safe_account = dict(account)
            if "login" in safe_account:
                safe_account["login"] = "REDACTED"
            safe_report["account"] = safe_account
        safe["report"] = safe_report
    safe["execution_authority"] = False
    return safe


def _safe_source(name: str, fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Convert source failures into blocked evidence, never readiness."""
    try:
        value = fn()
        return value if isinstance(value, dict) else {
            "status": "ERROR", "source": name, "error": "source returned a non-object",
            "execution_authority": False,
        }
    except Exception as exc:  # noqa: BLE001 - readiness must remain diagnostic
        return {
            "status": "ERROR", "source": name,
            "error": "evidence source unavailable",
            "error_type": type(exc).__name__,
            "execution_authority": False,
        }


# The security scan walks the whole checkout and can take minutes on large or
# OneDrive-backed roots, which dominated boot VALIDATE. Kick it off early (it
# runs while the DB sources evaluate) and give it a short bounded wait at the
# point where its evidence is consumed. A prefetch that misses the deadline is
# reported as PENDING_BACKGROUND — fail-closed (it can never look like a pass),
# while the daemon thread finishes and warms the audit's cross-process disk
# cache so subsequent boots resolve instantly.
_SECURITY_WAIT_SECONDS = 8.0


def _sa_module() -> Any:
    """The security_audit module object, whichever import path won at startup.

    The module-level name ``security_audit`` here is the imported *function*;
    its ``__module__`` names the defining module in ``sys.modules``, which is
    the shared object whose cache/audit surface this file consumes.
    """
    owner = getattr(security_audit, "__module__", None) or getattr(security_audit, "__name__", None)
    return sys.modules.get(owner) if owner else None


def _load_prefetch_disk_report(db_root: str) -> Dict[str, Any]:
    """Fire-and-forget warmup for the security_audit disk cache.

    Runs ``audit(db_root)`` in a daemon thread: a warm disk cache makes it a
    cheap read, a cold one schedules the full scan off the caller's critical
    path. The result is only consumed via the audit's own caches.
    """
    _sa = _sa_module()

    def _run() -> None:
        try:
            _sa.audit(db_root)
        except Exception:  # noqa: BLE001 - prefetch is best-effort
            pass

    thread = threading.Thread(target=_run, name="security-audit-prefetch", daemon=True)
    thread.start()
    return {"thread": thread}


def _security_source(db_root: str, wait_for_prefetch: Callable[[], bool]) -> Dict[str, Any]:
    """Security evidence with a bounded wait on the early prefetch.

    The audit's own in-process cache makes a post-prefetch direct call cheap
    when the scan completed in time; otherwise the bounded wait gives the
    daemon a final grace window. If evidence is still not ready the report is
    PENDING_BACKGROUND: an explicit blocker, never a silent pass.
    """
    _sa = _sa_module()

    def _direct() -> Dict[str, Any]:
        return _sa.audit(db_root)  # type: ignore[attr-defined]

    # Warm L1 from the (possibly finished) prefetch without blocking first.
    try:
        inproc = _sa._audit_cache  # type: ignore[attr-defined]
        ts = _sa._audit_cache_ts  # type: ignore[attr-defined]
        if inproc is not None and (time.time() - ts) < _sa._AUDIT_CACHE_TTL:  # type: ignore[attr-defined]
            return inproc
    except AttributeError:
        pass
    if wait_for_prefetch():
        return _direct()
    if _sa._audit_cache is not None:  # type: ignore[attr-defined]
        return _sa._audit_cache  # type: ignore[attr-defined]
    return {
        "status": "PENDING_BACKGROUND",
        "source": "security_audit",
        "detail": "scan still running in background; result will warm the disk cache",
        "values_redacted": True,
        "execution_authority": False,
    }


def build(path: str = "trading_state.db", *, auth_fn: Callable[[], Dict[str, Any]] | None = None) -> Dict[str, Any]:
    auth = _safe_source("authorization", auth_fn or auth_report)
    # Authorization evidence and order authority are deliberately separate:
    # auth diagnostics may prove the terminal/account is connected, but they
    # must never grant permission to place an order.
    auth_verified = bool(
        str(auth.get("status", "")).upper() == "AUTHORIZED"
        and str((auth.get("probe") or {}).get("status", "AUTHORIZED")).upper() == "AUTHORIZED"
    )
    # Start the slow security scan NOW so it overlaps the DB-bound sources.
    # The scan root is the DB's directory so a local/testing database never
    # walks the entire project tree.
    db_root = str(Path(path).resolve().parent)
    _prefetch = _load_prefetch_disk_report(db_root)
    audit = _safe_source("journal_audit", lambda: audit_journal(path))
    legacy = _safe_source("legacy_reconciliation", lambda: legacy_report(path))
    sample = _safe_source("sample_gate", lambda: sample_report(path))
    live_rollout = _safe_source("live_rollout", lambda: live_rollout_status(path))
    # db_root was computed early to start the security prefetch; the scan is
    # scoped to the DB's directory (see above).

    def _wait_for_prefetch() -> bool:
        _prefetch["thread"].join(timeout=_SECURITY_WAIT_SECONDS)
        return not _prefetch["thread"].is_alive()

    security = _safe_source(
        "security_audit",
        lambda: _security_source(db_root, _wait_for_prefetch),
    )
    research_influence = _safe_source(
        "research_influence",
        lambda: research_influence_status(path, model_kind="all"),
    )
    # S.Ξ trial-ledger tamper-evidence: read-only hash-chain verification of
    # both ledger DBs (the DSR gate's audit trail). A broken chain means the
    # multiple-testing bar can no longer be trusted — blocker. ABSENT is
    # fine (no ledger yet = nothing to tamper with).
    def _chain_gate() -> Dict[str, Any]:
        main = _verify_chain_main()
        cpcv = _verify_chain_cpcv()
        valid = bool(main.get("valid")) and bool(cpcv.get("valid"))
        statuses = {"trial_ledger": main.get("status"),
                    "strategy_trial_ledger": cpcv.get("status")}
        return {
            "status": "OK" if valid else "COMPROMISED",
            "valid": valid,
            "ledgers": statuses,
            "trial_ledger": main,
            "strategy_trial_ledger": cpcv,
            "execution_authority": False,
        }

    ledger_chain = _safe_source("trial_ledger_chain", _chain_gate)
    preflight_envelope = {}
    try:
        from core.trading_safety import TradingStore
        store = TradingStore(path)
        preflight_envelope = store.get_state("broker_preflight", {}) or {}
    except Exception as exc:
        preflight_envelope = {"error": str(exc)}
    preflight_report = preflight_envelope.get("report") if isinstance(preflight_envelope, dict) else {}
    captured_symbols = preflight_report.get("symbols") if isinstance(preflight_report, dict) else {}
    symbols = sorted(captured_symbols.keys()) if isinstance(captured_symbols, dict) else []
    try:
        preflight = validate_envelope(preflight_envelope, symbols)
    except Exception as exc:  # noqa: BLE001 - malformed evidence is a blocker
        preflight = {"valid": False, "status": "ERROR",
                     "reasons": ["broker preflight evidence is malformed"],
                     "error_type": type(exc).__name__, "execution_authority": False}

    blockers = []
    if not auth_verified:
        blockers.append("MT5 terminal/account authorization is not verified")
    if not bool(preflight.get("valid")):
        blockers.append("broker preflight is missing, stale, or invalid")
    if legacy.get("operator_action_required"):
        blockers.append("legacy open records require reconciliation or quarantine review")
    if not bool(audit.get("passed")):
        blockers.append("journal integrity audit failed")
    if sample.get("status") != "READY_FOR_RESEARCH":
        blockers.append("labelled point-in-time sample is not sufficient for research promotion")
    if not bool(live_rollout.get("active")):
        blockers.append("tiny-live rollout approval is absent, expired, or evidence-invalid")
    _security_pending = str(security.get("status", "")).upper() == "PENDING_BACKGROUND"
    if _security_pending:
        blockers.append("credential or trace security review is still scanning in the background (PENDING_BACKGROUND; rerun readiness once the scan warms the cache)")
    elif security.get("status") != "CLEAR_FOR_OPERATOR_REVIEW":
        blockers.append("credential or trace security review is incomplete")
    if not bool(research_influence.get("allowed")):
        blockers.append("learned/adaptive research influence is not approved")
    if not bool(ledger_chain.get("valid")):
        _chain = ledger_chain
        _detail = ""
        for _name in ("trial_ledger", "strategy_trial_ledger"):
            _sub = _chain.get(_name) or {}
            if not _sub.get("valid", True):
                _detail = (
                    f"{_name}: {_sub.get('status', 'ERROR')}"
                    + (f" at trial {_sub['broken_at']}" if _sub.get("broken_at") else "")
                )
                break
        blockers.append(f"trial-ledger hash chain compromised or unreadable ({_detail or 'evidence unavailable'})")

    return {
        "status": "READY_FOR_REVIEW" if not blockers else "BLOCKED",
        "execution_authority": False,
        "blockers": blockers,
        "next_actions": [
            "Repair MT5 authorization and capture broker preflight" if not auth_verified else "Keep authorization evidence current",
            "Reconcile or quarantine legacy records before using the journal for management" if legacy.get("operator_action_required") else "No legacy review is pending",
            "Collect correctly labelled, broker-reconciled closed trades across regimes" if sample.get("status") != "READY_FOR_RESEARCH" else "Run offline walk-forward and purged validation",
            "Obtain fresh explicit tiny-live approval after all evidence gates pass" if not live_rollout.get("active") else "Keep tiny-live approval evidence current",
            "Credential/trace security review is still scanning in the background; rerun readiness shortly" if _security_pending else ("Rotate remaining credential findings and verify redacted traces" if security.get("status") != "CLEAR_FOR_OPERATOR_REVIEW" else "Keep credential and trace review current"),
            "Keep HMM/GARCH/Kelly/RL advisory-only until sample, OOS evidence, digest, and operator approval pass" if not research_influence.get("allowed") else "Keep research-influence approval current",
            "Audit the ledger break, quarantine affected trials, and rebuild the chain from an encrypted backup" if not ledger_chain.get("valid") else "Keep trial-ledger tamper evidence intact",
        ],
        "gates": {
            "authorization": auth,
            "broker_preflight": {"validation": preflight, "envelope": _redact_preflight_envelope(preflight_envelope)},
            "journal_audit": audit,
            "legacy_reconciliation": legacy,
            "sample_gate": sample,
            "live_rollout": live_rollout,
            "security": security,
            "research_influence": research_influence,
            "trial_ledger_chain": ledger_chain,
        },
        "limitation": "Read-only readiness evidence; this report never grants order permission.",
    }
