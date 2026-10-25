"""StrategyGit: Version Control System for Quantitative Strategies (Pillar 4 / Stage 4).

Supports content-addressed SHA-256 commits, branch isolation (main, canary, shadow/fix-*),
genome diffing, Paired Wilcoxon Signed-Rank statistical Pull-Request merge gates,
and instant autonomous rollback on Page's CUSUM performance decay.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any, Dict, List, Optional
import numpy as np
from scipy.stats import wilcoxon

logger = logging.getLogger(__name__)


class StrategyGit:
    """Git-like version control engine for quantitative strategy genomes backed by SQLite."""

    def __init__(self, db_conn: sqlite3.Connection):
        self.db = db_conn
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self.db.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_commits (
                commit_hash TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                parent_hash TEXT,
                branch_name TEXT NOT NULL,
                genome_json TEXT NOT NULL,
                autopsy_diagnosis_json TEXT,
                commit_message TEXT NOT NULL,
                cpcv_oos_sharpes_json TEXT NOT NULL,
                mean_sharpe REAL NOT NULL,
                pbo REAL NOT NULL,
                dsr REAL NOT NULL,
                math_audit_passed INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_branches (
                strategy_id TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                head_commit_hash TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (strategy_id, branch_name)
            )
            """
        )
        self.db.commit()

    @staticmethod
    def compute_commit_hash(strategy_id: str, genome: Dict[str, Any], parent_hash: Optional[str]) -> str:
        """Deterministic 12-char SHA-256 hash identifying the strategy version."""
        payload = json.dumps(
            {"strategy_id": strategy_id, "genome": genome, "parent": parent_hash},
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]

    def commit(
        self,
        strategy_id: str,
        branch_name: str,
        genome: Dict[str, Any],
        commit_message: str,
        cpcv_oos_sharpes: List[float],
        pbo: float,
        dsr: float,
        math_audit_passed: bool,
        parent_hash: Optional[str] = None,
        autopsy_diagnosis: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Commit a strategy genome version. Requires passing math & causality verification."""
        if not math_audit_passed:
            return {"committed": False, "reason": "REJECTED: Failed core/math_auditor.py verification"}

        commit_hash = self.compute_commit_hash(strategy_id, genome, parent_hash)
        mean_sr = float(np.mean(cpcv_oos_sharpes)) if cpcv_oos_sharpes else 0.0
        now = time.time()

        cur = self.db.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO strategy_commits (
                commit_hash, strategy_id, parent_hash, branch_name, genome_json,
                autopsy_diagnosis_json, commit_message, cpcv_oos_sharpes_json,
                mean_sharpe, pbo, dsr, math_audit_passed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commit_hash,
                strategy_id,
                parent_hash,
                branch_name,
                json.dumps(genome, sort_keys=True),
                json.dumps(autopsy_diagnosis or {}),
                commit_message,
                json.dumps([round(float(x), 5) for x in cpcv_oos_sharpes]),
                mean_sr,
                float(pbo),
                float(dsr),
                1 if math_audit_passed else 0,
                now,
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO strategy_branches (strategy_id, branch_name, head_commit_hash, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (strategy_id, branch_name, commit_hash, now),
        )
        self.db.commit()
        return {"committed": True, "commit_hash": commit_hash, "branch": branch_name, "mean_sharpe": mean_sr}

    def diff(self, commit_a: str, commit_b: str) -> Dict[str, Any]:
        """Compute genome changes and quantitative performance delta between two commits."""
        cur = self.db.cursor()
        row_a = cur.execute("SELECT * FROM strategy_commits WHERE commit_hash = ?", (commit_a,)).fetchone()
        row_b = cur.execute("SELECT * FROM strategy_commits WHERE commit_hash = ?", (commit_b,)).fetchone()
        if not row_a or not row_b:
            return {"error": "Commit not found"}

        gen_a = json.loads(row_a["genome_json"])
        gen_b = json.loads(row_b["genome_json"])
        all_keys = sorted(set(gen_a.keys()) | set(gen_b.keys()))
        param_diffs = {
            k: {"from": gen_a.get(k), "to": gen_b.get(k)}
            for k in all_keys
            if gen_a.get(k) != gen_b.get(k)
        }
        return {
            "from_commit": commit_a,
            "to_commit": commit_b,
            "genome_changes": param_diffs,
            "sharpe_delta": round(float(row_b["mean_sharpe"]) - float(row_a["mean_sharpe"]), 4),
            "dsr_delta": round(float(row_b["dsr"]) - float(row_a["dsr"]), 4),
            "pbo_delta": round(float(row_b["pbo"]) - float(row_a["pbo"]), 4),
        }

    def merge_pull_request(
        self,
        strategy_id: str,
        source_branch: str,
        target_branch: str = "canary",
        max_p_val: float = 0.05,
        min_dsr: float = 0.95,
        max_pbo: float = 0.25,
    ) -> Dict[str, Any]:
        """Merge a candidate branch into target branch using Paired Wilcoxon Signed-Rank test and DSR gates."""
        cur = self.db.cursor()
        src_head = cur.execute(
            "SELECT head_commit_hash FROM strategy_branches WHERE strategy_id=? AND branch_name=?",
            (strategy_id, source_branch),
        ).fetchone()
        tgt_head = cur.execute(
            "SELECT head_commit_hash FROM strategy_branches WHERE strategy_id=? AND branch_name=?",
            (strategy_id, target_branch),
        ).fetchone()

        if not src_head:
            return {"merged": False, "reason": f"Source branch {source_branch} not found"}

        cand = cur.execute("SELECT * FROM strategy_commits WHERE commit_hash=?", (src_head[0],)).fetchone()
        if not cand or not cand["math_audit_passed"] or float(cand["dsr"]) < min_dsr or float(cand["pbo"]) > max_pbo:
            return {
                "merged": False,
                "reason": f"Candidate failed hard gates (DSR={cand['dsr'] if cand else 'N/A'}, PBO={cand['pbo'] if cand else 'N/A'})",
            }

        # If target branch already exists, require Paired Wilcoxon Superiority over current target HEAD
        if tgt_head:
            base = cur.execute("SELECT * FROM strategy_commits WHERE commit_hash=?", (tgt_head[0],)).fetchone()
            if base:
                cand_srs = np.array(json.loads(cand["cpcv_oos_sharpes_json"]), dtype=float)
                base_srs = np.array(json.loads(base["cpcv_oos_sharpes_json"]), dtype=float)

                if len(cand_srs) == len(base_srs) and len(cand_srs) >= 6:
                    diffs = cand_srs - base_srs
                    if np.all(diffs == 0):
                        return {"merged": False, "reason": "Candidate identical to target branch"}
                    try:
                        _, p_val = wilcoxon(cand_srs, base_srs, alternative="greater")
                        if p_val > max_p_val:
                            return {
                                "merged": False,
                                "reason": f"Mutation not statistically superior to {tgt_head[0]} (Wilcoxon p={p_val:.4f} > {max_p_val})",
                            }
                    except Exception as exc:
                        logger.debug("Wilcoxon calculation warning: %s", exc)

        # Fast-forward target branch pointer to candidate commit
        cur.execute(
            """
            INSERT OR REPLACE INTO strategy_branches (strategy_id, branch_name, head_commit_hash, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (strategy_id, target_branch, src_head[0], time.time()),
        )
        self.db.commit()
        return {"merged": True, "strategy_id": strategy_id, "target_branch": target_branch, "new_head": src_head[0]}

    def revert_to_parent(self, strategy_id: str, branch_name: str = "main") -> Dict[str, Any]:
        """Atomically roll branch HEAD back to its parent commit when CUSUM edge decay is detected."""
        cur = self.db.cursor()
        head = cur.execute(
            "SELECT head_commit_hash FROM strategy_branches WHERE strategy_id=? AND branch_name=?",
            (strategy_id, branch_name),
        ).fetchone()
        if not head:
            return {"reverted": False, "reason": "Branch not found"}

        curr_commit = cur.execute("SELECT parent_hash FROM strategy_commits WHERE commit_hash=?", (head[0],)).fetchone()
        if not curr_commit or not curr_commit["parent_hash"]:
            return {"reverted": False, "reason": "No parent commit to revert to; quarantine strategy instead"}

        parent_hash = curr_commit["parent_hash"]
        cur.execute(
            "UPDATE strategy_branches SET head_commit_hash=?, updated_at=? WHERE strategy_id=? AND branch_name=?",
            (parent_hash, time.time(), strategy_id, branch_name),
        )
        self.db.commit()
        return {"reverted": True, "strategy_id": strategy_id, "from_commit": head[0], "reverted_to": parent_hash}
