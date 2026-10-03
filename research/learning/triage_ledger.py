"""Active-learning persistence for per-article Laya triage disagreements.

Bridges the realtime news triage gate into the two existing learning stores:

1. Disagreement ledger — appends into
   ``macro_active_learning_disagreements`` inside ``artifacts/llm_decisions.db``,
   the same SQLite table ``DeterministicPostValidator.log_disagreement`` already
   writes for macro-cycle decisions. One row per (article, question) where the
   Laya label survived post-validation but differs from the deterministic rule.
2. Contrastive pair pool — seeds the RLCD training pool
   (``artifacts/rlcd_training_pool.db`` via ``ContrastivePairMiner``) so the next
   fine-tuning batch can consume article triage pairs. D0_1 spoof/social
   disagreements are logged as SAFETY_NEGATIVE (5x weight, mirroring the miner's
   crisis weighting); other disagreements as NEGATIVE.

All writes are best-effort: triage gating must never fail because the ledger is
unavailable. Nothing here grants execution authority.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from news_triage import D5A_RANK, D9_KILL_SWITCH_ACTIVE, REJECT_D0_1, _D9_TRUTHY
from research.learning.contrastive_pair_miner import ContrastivePair, ContrastivePairMiner

log = logging.getLogger("TriageActiveLearning")

DEFAULT_LEDGER_DB = "artifacts/llm_decisions.db"

_TRUSTED_D0_1 = {"TRUSTED_OFFICIAL", "VERIFIED_OUTLET", "OFFICIAL_VERIFIED"}

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class TriageDisagreementLogger:
    """Append triage (article, question) disagreements to the shared ledger."""

    def __init__(
        self,
        db_path: str = DEFAULT_LEDGER_DB,
        miner: Optional[ContrastivePairMiner] = None,
    ) -> None:
        self.db_path = db_path
        self._miner = miner
        self._session_ledger_rows = 0
        self._session_pairs = 0
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the shared disagreements table if missing (idempotent)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS macro_active_learning_disagreements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        event_name TEXT NOT NULL,
                        z_score REAL,
                        vix REAL,
                        question_id TEXT NOT NULL,
                        model_prediction TEXT NOT NULL,
                        deterministic_override TEXT NOT NULL,
                        override_reason TEXT NOT NULL,
                        features_json TEXT NOT NULL
                    )"""
                )
                conn.commit()
        except Exception as exc:
            log.warning("Triage ledger unavailable: %s", exc)
            self.db_path = ""

    @property
    def miner(self) -> Optional[ContrastivePairMiner]:
        if self._miner is None:
            try:
                self._miner = ContrastivePairMiner()
            except Exception as exc:
                log.warning("ContrastivePairMiner unavailable: %s", exc)
        return self._miner

    def log_verdict(self, verdict: Any, article: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """Persist one verdict's disagreements to ledger + RLCD pool.

        Returns counts: ``{"ledger_rows": N, "pairs_persisted": M}``.
        Never raises: a broken learning store must not break triage.
        """
        counts = {"ledger_rows": 0, "pairs_persisted": 0}
        try:
            d0_1 = str(getattr(verdict, "d0_1", "") or "")
            if getattr(verdict, "source", "") != "laya":
                return counts
            disagreements = list(getattr(verdict, "disagreement_ids", []) or [])
            if not disagreements:
                return counts

            answers = dict(getattr(verdict, "laya_answers", {}) or {})
            # Pre-validation deterministic labels (fall back to post-validation
            # fields for verdicts produced before this field existed).
            det_labels = dict(getattr(verdict, "deterministic_labels", {}) or {}) or {
                "D0_1_source_credibility": d0_1,
                "D0_2_duplicate_detection": str(getattr(verdict, "d0_2", "") or ""),
                "D0_3_fact_vs_opinion": str(getattr(verdict, "d0_3", "") or ""),
                "D5a_escalation_ladder": str(getattr(verdict, "d5a", "") or ""),
            }
            article = article if isinstance(article, dict) else {}
            title = str(article.get("title") or "untitled")[:240]
            dedupe_key = str(article.get("dedupe_key") or "")
            url = str(article.get("url") or "")
            publisher = str(article.get("publisher") or article.get("provider") or "")
            published_at = str(article.get("published_at") or "")
            summary = str(article.get("text_or_summary") or "")[:1000]

            # Deterministic pair-id seeds: stable per (article, question) so the
            # pool's INSERT OR IGNORE dedupes identical re-disagreements.
            base_seed = f"{dedupe_key}|{url}|{title}"

            ledger_rows: List[tuple] = []
            pairs: List[ContrastivePair] = []
            prompt_state = dict(getattr(verdict, "prompt_state", {}) or {})
            prompt_context = {
                "kind": "news_triage_article",
                "title": title,
                "url": url,
                "publisher": publisher[:120],
                "provider": str(article.get("provider") or "")[:80],
                "published_at": published_at,
                "summary_excerpt": summary,
                "dedupe_key": dedupe_key,
                "triage_prompt_state": prompt_state,
            }
            f_hash = hashlib.sha256(json.dumps(prompt_context, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]

            for qid in disagreements:
                model_label = str(answers.get(qid, "") or "")
                det_label = det_labels.get(qid, "")
                if not model_label or not det_label or model_label == det_label:
                    continue

                # Deterministic label is the ground truth by doctrine (same
                # convention as mine_from_outcome: chosen = deterministic
                # ground truth, rejected = the model's raw label). Monotone
                # validation guarantees a REJECT-class deterministic label is a
                # floor, so any disagreement there means the model tried to
                # ingest a spoofed/unverified source — that is the safety pair.
                # Same doctrine for D5a: a disagreement where the model tried
                # to DE-escalate below the deterministic ladder floor is a
                # hazard-understatement signal (5x weight). For D9 the
                # deterministic kill-switch tripwire is a hard floor: a model
                # answer that un-kills a fired EMERGENCY_HALT_ACTIVE is the
                # hazard signal (5x weight). (In the live path the D9
                # short-circuit returns before the model runs; this predicate
                # guards other verdict sources.)
                is_safety = (
                    qid == "D0_1_source_credibility" and det_label in REJECT_D0_1
                ) or (
                    qid == "D5a_escalation_ladder"
                    and det_label in D5A_RANK
                    and model_label in D5A_RANK
                    and D5A_RANK[model_label] < D5A_RANK[det_label]
                ) or (
                    qid == "D9_emergency_kill_switch"
                    and det_label == D9_KILL_SWITCH_ACTIVE
                    and model_label.upper() not in _D9_TRUTHY
                )
                pair_id = hashlib.sha256(f"{base_seed}|{qid}".encode("utf-8")).hexdigest()[:16]
                pairs.append(
                    ContrastivePair(
                        pair_id=pair_id,
                        decision_id=qid,
                        pair_type="SAFETY_NEGATIVE" if is_safety else "NEGATIVE",
                        prompt_context=prompt_context,
                        chosen_label=det_label,
                        rejected_label=model_label,
                        sampling_weight=5.0 if is_safety else 1.5,
                        feature_hash=f_hash,
                        source_ref=dedupe_key or url or title,
                    )
                )

                ledger_rows.append((
                    _utcnow(), f"news_triage:{title}", None, None, qid,
                    model_label, det_label,
                    "Laya triage override of deterministic Layer-0 label",
                    json.dumps({"dedupe_key": dedupe_key, "url": url, "publisher": publisher[:120],
                                "published_at": published_at, "summary_excerpt": summary,
                                "prompt_state": prompt_state}),
                ))

            if ledger_rows and self.db_path:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.executemany(
                            """INSERT INTO macro_active_learning_disagreements (
                                created_at, event_name, z_score, vix, question_id,
                                model_prediction, deterministic_override, override_reason, features_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            ledger_rows,
                        )
                        conn.commit()
                    counts["ledger_rows"] = len(ledger_rows)
                    self._session_ledger_rows += counts["ledger_rows"]
                except Exception as exc:
                    log.warning("Failed to log triage disagreements: %s", exc)

            miner = self.miner
            if pairs and miner is not None:
                try:
                    counts["pairs_persisted"] = miner.persist_pairs(pairs)
                    self._session_pairs += counts["pairs_persisted"]
                except Exception as exc:
                    log.warning("Failed to persist triage contrastive pairs: %s", exc)

        except Exception as exc:
            log.debug("Triage disagreement logging skipped: %s", exc)
        return counts

    def summary(self) -> Dict[str, Any]:
        """Session counters for the ingest report (never raises)."""
        return {
            "status": "ACTIVE",
            "ledger_db": self.db_path or "unavailable",
            "ledger_rows": self._session_ledger_rows,
            "pairs_persisted": self._session_pairs,
            "execution_authority": False,
        }

    def get_recent_disagreements(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most recent triage disagreements (debug/eval helper)."""
        if not self.db_path:
            return []
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT created_at, event_name, question_id, model_prediction,
                              deterministic_override, override_reason, features_json
                       FROM macro_active_learning_disagreements
                       WHERE event_name LIKE 'news_triage:%'
                       ORDER BY id DESC LIMIT ?""",
                    (max(1, int(limit)),),
                ).fetchall()
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for created, event, qid, model_pred, det, reason, feats in rows:
            out.append({
                "created_at": created, "event_name": event, "question_id": qid,
                "model_prediction": model_pred, "deterministic_override": det,
                "override_reason": reason,
                "features": json.loads(feats) if feats else {},
            })
        return out


def make_verdict_logger(logger: Optional[TriageDisagreementLogger] = None):
    """Build the on_verdict callback for ``triage_articles``.

    None (default) => no-op callback; never raises from the caller's view.
    """
    if logger is None:
        return None

    def _on_verdict(verdict: Any, article: Optional[Dict[str, Any]]) -> None:
        logger.log_verdict(verdict, article)

    return _on_verdict
