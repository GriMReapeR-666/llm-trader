"""Meta-Loop (Loop 4) — The Self-Evolution Engine

UAQP Loop 4: The system improves its own research process over time.

Architecture (from UAQP Patch 3):
    μ logs all (h, S.B, live_outcome) tuples to meta-corpus C_meta
        ↓
    Periodic meta-analysis (monthly/quarterly):
      → Identify patterns: "Hypotheses from source X decay faster in regime Y"
      → Identify patterns: "Signals with feature type Z overfit 3× more"
        ↓
    Meta-analysis proposes: Δpriors = {Δα_weights, Δβ_thresholds, Δρ_sizing}
        ↓
    Δpriors is NOT applied directly
        ↓
    Treat Δpriors as a hypothesis:
      shadow-deploy against held-out historical window
      run through DSR (Deflated Sharpe) validation
        ↓
    If shadow DSR ≥ τ_dsr → Δpriors applied, logged with justification
    If shadow DSR < τ_dsr → Δpriors discarded, logged as rejected meta-hypothesis

Key invariant (from Patch 3):
    β's own thresholds are only ever loosened by something that itself
    survived β's standard. The recursion terminates instead of compounding.

Properties:
    - Append-only meta-corpus (no entries deleted)
    - Shadow-deploy gate prevents self-referential loosening
    - Pattern mining uses simple frequency + correlation analysis
    - All Δpriors proposals and outcomes are logged for audit
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.state_persistence import StoreBackedState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STATE_FILE = "meta_loop_state.json"
DEFAULT_DB_FILE = "meta_corpus.db"
DEFAULT_DSR_THRESHOLD = 0.95  # τ_dsr — shadow deploy must pass this
DEFAULT_MIN_SAMPLES = 10  # minimum corpus entries before meta-analysis runs
DEFAULT_SHADOW_WINDOW = 50  # held-out observations for shadow deploy
DEFAULT_ANALYSIS_INTERVAL_DAYS = 30  # run meta-analysis monthly


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------

class PriorType(str, Enum):
    ALPHA_WEIGHTS = "alpha_weights"
    BETA_THRESHOLDS = "beta_thresholds"
    RHO_SIZING = "rho_sizing"


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    SHADOW_TESTING = "SHADOW_TESTING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PatternType(str, Enum):
    REGIME_DECAY = "regime_decay"          # alpha decays faster in regime X
    SOURCE_OVERFIT = "source_overfit"      # source X overfits more
    FEATURE_FRAGILE = "feature_fragile"    # feature type Z is fragile
    HORIZON_MISMATCH = "horizon_mismatch"  # holding horizon wrong for regime
    SIZE_BIAS = "size_bias"               # sizing model biased in regime


@dataclass
class CorpusEntry:
    """A single entry in the meta-corpus C_meta.

    Every (hypothesis, backtest_result, live_outcome) tuple gets logged here.
    """
    entry_id: str = ""
    hypothesis_id: str = ""
    hypothesis_source: str = ""  # e.g., "alpha_discovery", "manual", "evolutionary"
    hypothesis_expression: str = ""
    regime_at_entry: str = ""
    backtest_sharpe: float = 0.0
    backtest_max_dd: float = 0.0
    backtest_win_rate: float = 0.0
    backtest_n_trades: int = 0
    live_sharpe: float = 0.0
    live_pnl: float = 0.0
    live_max_dd: float = 0.0
    live_n_trades: int = 0
    decay_status: str = ""  # HEALTHY, DEGRADED, DEAD, REVERSED
    ic_raw: float = 0.0
    ic_purified: float = 0.0
    feature_type: str = ""  # momentum, mean_reversion, volatility, etc.
    holding_horizon: str = ""  # M5, M15, H1, H4, D1
    alpha_weights_snapshot: Dict[str, float] = field(default_factory=dict)
    logged_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Pattern:
    """A discovered pattern in the meta-corpus."""
    pattern_type: PatternType
    description: str
    evidence_count: int
    confidence: float  # 0-1
    affected_items: List[str] = field(default_factory=list)  # hypothesis IDs
    recommendation: str = ""
    discovered_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pattern_type"] = self.pattern_type.value
        return d


@dataclass
class DeltaPriors:
    """Proposed changes to global parameters.

    Δpriors = {Δα_weights, Δβ_thresholds, Δρ_sizing}

    This is a PROPOSAL, not applied directly. Must survive shadow-deploy
    validation before being applied.
    """
    proposal_id: str = ""
    prior_type: PriorType = PriorType.ALPHA_WEIGHTS
    proposed_changes: Dict[str, Any] = field(default_factory=dict)
    justification: str = ""
    pattern_ids: List[str] = field(default_factory=list)  # which patterns led to this
    status: ProposalStatus = ProposalStatus.PROPOSED
    shadow_dsr: float = 0.0
    shadow_sharpe: float = 0.0
    shadow_max_dd: float = 0.0
    applied_at: str = ""
    rejected_at: str = ""
    rejection_reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["prior_type"] = self.prior_type.value
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# Meta-Corpus (C_meta) — SQLite-backed append-only log
# ---------------------------------------------------------------------------

class MetaCorpus:
    """Append-only meta-corpus C_meta.

    Stores every (hypothesis, backtest, live_outcome) tuple.
    No UPDATE or DELETE operations — immutable by design.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or os.path.join(
            os.path.dirname(__file__), "..", DEFAULT_DB_FILE
        )
        self._init_db()

    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._conn() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS corpus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_json TEXT NOT NULL,
                    hash_chain TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS proposals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            con.commit()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    def _last_hash(self) -> str:
        with self._conn() as con:
            row = con.execute(
                "SELECT hash_chain FROM corpus ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["hash_chain"] if row else "genesis"

    # Serialize read-last-hash -> compute -> insert -> commit inside this
    # process.  Cross-process safety comes from BEGIN IMMEDIATE, which takes
    # the DB RESERVED lock BEFORE the last-hash read, so two writers cannot
    # chain from the same head.  (This closes the Sep-19 corpus break at
    # row 131 — same read-then-insert race class as the Sep-14 trial-ledger
    # break: a second writer read a stale prev_hash and linked to a head
    # that no longer existed once the first writer committed.)
    _append_lock = threading.Lock()

    def log_entry(self, entry: CorpusEntry) -> int:
        """Append a corpus entry (immutable, hash-chained, race-safe)."""
        import hashlib
        entry_json = json.dumps(entry.to_dict(), sort_keys=True)
        with self._append_lock:
            con = sqlite3.connect(self._db_path)
            con.row_factory = sqlite3.Row
            try:
                con.execute("BEGIN IMMEDIATE")
                try:
                    row = con.execute(
                        "SELECT hash_chain FROM corpus ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    prev_hash = row["hash_chain"] if row else "genesis"
                    chain_input = f"{prev_hash}:{entry_json}"
                    chain_hash = hashlib.sha256(
                        chain_input.encode()).hexdigest()[:16]
                    cur = con.execute(
                        "INSERT INTO corpus (entry_json, hash_chain) "
                        "VALUES (?, ?)",
                        (entry_json, chain_hash),
                    )
                    new_id = cur.lastrowid
                    con.commit()
                except Exception:
                    con.rollback()
                    raise
            finally:
                con.close()
        # Post-commit link verification (catches any residual cross-process
        # race loudly instead of silently corrupting the chain).
        with self._conn() as con:
            row = con.execute(
                "SELECT hash_chain FROM corpus WHERE id=?", (new_id,)
            ).fetchone()
            if row is None or row["hash_chain"] != chain_hash:
                raise RuntimeError(
                    f"corpus append link verification failed at id={new_id}; "
                    "concurrent writer raced the chain — investigate before "
                    "further appends"
                )
        return new_id  # type: ignore[return-value]

    def log_pattern(self, pattern: Pattern) -> int:
        """Append a discovered pattern."""
        pattern_json = json.dumps(pattern.to_dict(), sort_keys=True)
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO patterns (pattern_json) VALUES (?)", (pattern_json,)
            )
            con.commit()
            return cur.lastrowid  # type: ignore

    def log_proposal(self, proposal: DeltaPriors) -> int:
        """Append a Δpriors proposal."""
        proposal_json = json.dumps(proposal.to_dict(), sort_keys=True)
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO proposals (proposal_json) VALUES (?)", (proposal_json,)
            )
            con.commit()
            return cur.lastrowid  # type: ignore

    def get_entries(self, limit: int = 500) -> List[CorpusEntry]:
        """Retrieve recent corpus entries."""
        with self._conn() as con:
            rows = con.execute(
                "SELECT entry_json FROM corpus ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        entries = []
        for row in rows:
            d = json.loads(row["entry_json"])
            entries.append(CorpusEntry(**d))
        return entries

    def get_entry_count(self) -> int:
        with self._conn() as con:
            return con.execute("SELECT COUNT(*) FROM corpus").fetchone()[0]

    def get_patterns(self, limit: int = 100) -> List[Pattern]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT pattern_json FROM patterns ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Pattern(**json.loads(r["pattern_json"])) for r in rows]

    def get_proposals(self, limit: int = 100) -> List[DeltaPriors]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT proposal_json FROM proposals ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [DeltaPriors(**json.loads(r["proposal_json"])) for r in rows]

    def verify_chain(self) -> Dict[str, Any]:
        """Verify hash chain integrity of corpus entries."""
        import hashlib
        with self._conn() as con:
            rows = con.execute(
                "SELECT entry_json, hash_chain FROM corpus ORDER BY id"
            ).fetchall()

        if not rows:
            return {"valid": True, "entries": 0, "broken_at": None}

        prev_hash = "genesis"
        for i, row in enumerate(rows):
            chain_input = f"{prev_hash}:{row['entry_json']}"
            expected = hashlib.sha256(chain_input.encode()).hexdigest()[:16]
            if row["hash_chain"] != expected:
                return {"valid": False, "entries": len(rows),
                        "broken_at": i, "broken_id": i + 1}
            prev_hash = row["hash_chain"]

        return {"valid": True, "entries": len(rows), "broken_at": None}


# ---------------------------------------------------------------------------
# Pattern Miner
# ---------------------------------------------------------------------------

class PatternMiner:
    """Mine patterns from the meta-corpus.

    Identifies:
    - REGIME_DECAY: alphas that decay faster in specific regimes
    - SOURCE_OVERFIT: hypothesis sources with high IS/OOS divergence
    - FEATURE_FRAGILE: feature types with inconsistent live performance
    - HORIZON_MISMATCH: holding horizons that underperform in certain regimes
    """

    def __init__(self, corpus: MetaCorpus) -> None:
        self.corpus = corpus

    def mine_all(self) -> List[Pattern]:
        """Run all pattern detectors and return discovered patterns."""
        patterns: List[Pattern] = []
        patterns.extend(self._mine_regime_decay())
        patterns.extend(self._mine_source_overfit())
        patterns.extend(self._mine_feature_fragile())
        return patterns

    def _mine_regime_decay(self) -> List[Pattern]:
        """Find alphas that decay faster in specific regimes."""
        entries = self.corpus.get_entries(limit=500)
        if len(entries) < DEFAULT_MIN_SAMPLES:
            return []

        # Group by regime
        by_regime: Dict[str, List[CorpusEntry]] = defaultdict(list)
        for e in entries:
            if e.regime_at_entry:
                by_regime[e.regime_at_entry].append(e)

        patterns = []
        for regime, regime_entries in by_regime.items():
            if len(regime_entries) < 5:
                continue

            dead_count = sum(1 for e in regime_entries if e.decay_status in ("DEAD", "REVERSED"))
            ratio = dead_count / len(regime_entries)

            if ratio > 0.4:  # >40% dead in this regime
                affected = [e.hypothesis_id for e in regime_entries if e.decay_status in ("DEAD", "REVERSED")]
                patterns.append(Pattern(
                    pattern_type=PatternType.REGIME_DECAY,
                    description=f"{ratio:.0%} of alphas are DEAD/REVERSED in regime {regime}",
                    evidence_count=dead_count,
                    confidence=min(ratio, 1.0),
                    affected_items=affected[:20],
                    recommendation=f"Reduce alpha weights or increase decay threshold for regime {regime}",
                ))

        return patterns

    def _mine_source_overfit(self) -> List[Pattern]:
        """Find hypothesis sources with high IS/OOS divergence."""
        entries = self.corpus.get_entries(limit=500)
        if len(entries) < DEFAULT_MIN_SAMPLES:
            return []

        # Group by source
        by_source: Dict[str, List[CorpusEntry]] = defaultdict(list)
        for e in entries:
            if e.hypothesis_source:
                by_source[e.hypothesis_source].append(e)

        patterns = []
        for source, source_entries in by_source.items():
            if len(source_entries) < 5:
                continue

            # Compute average IS/OOS divergence
            divergences = []
            for e in source_entries:
                if e.backtest_sharpe > 0:
                    div = (e.backtest_sharpe - e.live_sharpe) / e.backtest_sharpe
                    divergences.append(div)

            if not divergences:
                continue

            avg_div = np.mean(divergences)
            if avg_div > 0.5:  # >50% average degradation
                affected = [e.hypothesis_id for e in source_entries]
                patterns.append(Pattern(
                    pattern_type=PatternType.SOURCE_OVERFIT,
                    description=f"Source '{source}' shows {avg_div:.0%} avg IS/OOS degradation",
                    evidence_count=len(divergences),
                    confidence=min(avg_div, 1.0),
                    affected_items=affected[:20],
                    recommendation=f"Tighten validation thresholds for hypotheses from '{source}'",
                ))

        return patterns

    def _mine_feature_fragile(self) -> List[Pattern]:
        """Find feature types with inconsistent live performance."""
        entries = self.corpus.get_entries(limit=500)
        if len(entries) < DEFAULT_MIN_SAMPLES:
            return []

        # Group by feature type
        by_feature: Dict[str, List[CorpusEntry]] = defaultdict(list)
        for e in entries:
            if e.feature_type:
                by_feature[e.feature_type].append(e)

        patterns = []
        for feature, feature_entries in by_feature.items():
            if len(feature_entries) < 5:
                continue

            live_sharpes = [e.live_sharpe for e in feature_entries]
            if len(live_sharpes) < 3:
                continue

            # High variance = fragile
            variance = np.var(live_sharpes)
            mean_sharpe = np.mean(live_sharpes)

            if variance > 0.25 and mean_sharpe < 0.5:  # high variance + low mean
                affected = [e.hypothesis_id for e in feature_entries]
                patterns.append(Pattern(
                    pattern_type=PatternType.FEATURE_FRAGILE,
                    description=f"Feature '{feature}' has high variance ({variance:.3f}) and low mean Sharpe ({mean_sharpe:.3f})",
                    evidence_count=len(feature_entries),
                    confidence=min(variance / 0.5, 1.0),
                    affected_items=affected[:20],
                    recommendation=f"Reduce weight for feature type '{feature}' or add orthogonalization",
                ))

        return patterns


# ---------------------------------------------------------------------------
# Shadow Deploy — validates Δpriors before application
# ---------------------------------------------------------------------------

class ShadowDeploy:
    """Shadow-deploy Δpriors against held-out historical data.

    This is the critical gate from UAQP Patch 3:
    - Δpriors is NOT applied directly
    - Treat Δpriors as a hypothesis
    - Shadow-deploy against held-out window
    - Compute DSR on shadow results
    - Only apply if DSR ≥ τ_dsr

    This prevents the meta-loop from loosening β's own thresholds
    based on pattern-mining that itself was never tested.
    """

    def __init__(
        self,
        dsr_threshold: float = DEFAULT_DSR_THRESHOLD,
        shadow_window: int = DEFAULT_SHADOW_WINDOW,
        corpus: Optional[Any] = None,
    ) -> None:
        self.dsr_threshold = dsr_threshold
        self.shadow_window = shadow_window
        self.corpus = corpus

    def validate_proposal(
        self,
        proposal: DeltaPriors,
        current_weights: Dict[str, float],
        historical_entries: List[CorpusEntry],
    ) -> Tuple[bool, float, str]:
        """Shadow-deploy a Δpriors proposal against historical data.

        Args:
            proposal: The proposed parameter changes
            current_weights: Current alpha weights
            historical_entries: Historical corpus entries for shadow testing

        Returns:
            (passed, dsr_score, reason)
        """
        if len(historical_entries) < self.shadow_window:
            return (
                False,
                0.0,
                f"Insufficient historical data ({len(historical_entries)} < {self.shadow_window})",
            )

        # Use the last `shadow_window` entries as held-out data
        held_out = historical_entries[-self.shadow_window:]

        # Simulate what would have happened with proposed changes
        shadow_returns = self._simulate_shadow(proposal, current_weights, held_out)

        if len(shadow_returns) < 10:
            return (False, 0.0, "Shadow simulation produced insufficient returns")

        # Compute DSR-equivalent: use Sharpe as proxy for DSR
        shadow_sharpe = self._compute_shadow_sharpe(shadow_returns)
        shadow_dd = self._compute_max_drawdown(shadow_returns)

        # DSR proxy: penalize for drawdown and require minimum Sharpe
        dsr_score = self._compute_dsr_proxy(shadow_sharpe, shadow_dd, len(shadow_returns))

        passed = dsr_score >= self.dsr_threshold
        reason = (
            f"Shadow Sharpe={shadow_sharpe:.3f}, MaxDD={shadow_dd:.3f}, "
            f"DSR_proxy={dsr_score:.3f} (threshold={self.dsr_threshold})"
        )

        proposal.shadow_dsr = dsr_score
        proposal.shadow_sharpe = shadow_sharpe
        proposal.shadow_max_dd = shadow_dd

        return (passed, dsr_score, reason)

    def _simulate_shadow(
        self,
        proposal: DeltaPriors,
        current_weights: Dict[str, float],
        held_out: List[CorpusEntry],
    ) -> List[float]:
        """Simulate returns under proposed parameter changes."""
        simulated = []

        for entry in held_out:
            # Base return proxy from live Sharpe (annualized → per-trade)
            base_return = entry.live_sharpe / math.sqrt(252 * 24)  # M15 bars

            # Apply proposed weight changes
            adjustment = self._compute_weight_adjustment(
                proposal, entry.hypothesis_id, entry.feature_type
            )
            adjusted_return = base_return * adjustment
            simulated.append(adjusted_return)

        return simulated

    def _compute_weight_adjustment(
        self,
        proposal: DeltaPriors,
        hypothesis_id: str,
        feature_type: str,
    ) -> float:
        """Compute how much a proposed change would affect a specific hypothesis."""
        if proposal.prior_type == PriorType.ALPHA_WEIGHTS:
            # Check if this hypothesis is affected by the proposed changes
            changes = proposal.proposed_changes
            if hypothesis_id in changes:
                return changes[hypothesis_id]
            if feature_type in changes:
                return changes[feature_type]
            return 1.0  # no change

        elif proposal.prior_type == PriorType.BETA_THRESHOLDS:
            # Threshold changes affect all hypotheses proportionally
            scale = proposal.proposed_changes.get("scale", 1.0)
            return scale

        elif proposal.prior_type == PriorType.RHO_SIZING:
            # Sizing changes affect position sizes, not returns directly
            # But they affect risk-adjusted returns
            size_factor = proposal.proposed_changes.get("size_factor", 1.0)
            return size_factor

        return 1.0

    def _compute_shadow_sharpe(self, returns: List[float]) -> float:
        """Compute annualized Sharpe from simulated returns."""
        arr = np.array(returns)
        if arr.std() == 0:
            return 0.0
        return float(arr.mean() / arr.std() * math.sqrt(252))

    def _compute_max_drawdown(self, returns: List[float]) -> float:
        """Compute maximum drawdown from return series."""
        cumulative = np.cumprod(1 + np.array(returns))
        peak = np.maximum.accumulate(cumulative)
        drawdown = (cumulative - peak) / peak
        return float(abs(drawdown.min()))

    def _compute_dsr_proxy(
        self, sharpe: float, max_dd: float, n_obs: int
    ) -> float:
        """Compute DSR proxy using the real deflated_sharpe_test.

        Uses n_trials from corpus size for multiple-testing correction.
        Falls back to a corrected heuristic with Bonferroni-style penalty
        if overfit_detector is unavailable.
        """
        try:
            from core.overfit_detector import deflated_sharpe_test
            n_trials = max(1, self.corpus.get_entry_count())
            result = deflated_sharpe_test(
                estimated_sharpe=sharpe,
                n_trials=n_trials,
                sample_size=max(n_obs, 10),
                benchmark_sharpe=0.0,
            )
            # DSR test returns p_value (lower = more significant)
            # Convert to 0-1 score: p < 0.05 → score > 0.8, p > 0.5 → score < 0.2
            p = result.get("p_value", 0.5)
            score = 1.0 - min(p * 2, 1.0)
            return max(0.0, min(1.0, score))
        except ImportError as exc:
            logger.warning("overfit_detector unavailable, DSR gate uses degraded proxy: %s", exc)
        except Exception as exc:
            logger.warning("deflated_sharpe_test failed, DSR gate uses degraded proxy: %s", exc)
        # Fallback: Bonferroni-style multiple-testing correction
        n_trials = 1
        try:
            n_trials = max(1, self.corpus.get_entry_count())
        except Exception:
            pass
        sharpe_score = min(max(sharpe / 2.0, 0.0), 1.0)
        dd_penalty = min(max_dd, 1.0)
        # Multiple-testing penalty: Bonferroni divides alpha by n_trials
        # Use log(n_trials) as a softer alternative that doesn't over-penalize
        mt_penalty = max(0.0, 1.0 - math.log(max(n_trials, 1)) * 0.1)
        sample_bonus = min(n_obs / 100.0, 0.2)
        score = sharpe_score * (1 - dd_penalty * 0.5) * mt_penalty + sample_bonus
        return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Meta-Loop Orchestrator
# ---------------------------------------------------------------------------

class MetaLoop(StoreBackedState):
    """Loop 4 — The Self-Evolution Engine.

    Orchestrates the full meta-learning cycle:
    1. Log outcomes to C_meta
    2. Mine patterns periodically
    3. Propose Δpriors based on patterns
    4. Shadow-deploy proposals
    5. Apply or reject based on DSR gate

    Properties:
    - Append-only: no entries deleted from C_meta
    - Shadow-gated: Δpriors never applied without validation
    - Self-limiting: β's thresholds only tightened, never loosened by meta-loop
    """

    store_key = "meta_loop_state"

    def __init__(
        self,
        state_path: Optional[str] = None,
        db_path: Optional[str] = None,
        dsr_threshold: float = DEFAULT_DSR_THRESHOLD,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        shadow_window: int = DEFAULT_SHADOW_WINDOW,
        audit_logger: Optional[Any] = None,
        store: Optional[Any] = None,
    ) -> None:
        self._state_path = state_path or os.path.join(
            os.path.dirname(__file__), "..", DEFAULT_STATE_FILE
        )
        self.state_file = self._state_path
        self.store = store
        self.corpus = MetaCorpus(db_path=db_path)
        self.miner = PatternMiner(self.corpus)
        self.shadow = ShadowDeploy(
            dsr_threshold=dsr_threshold,
            shadow_window=shadow_window,
            corpus=self.corpus,
        )
        self.min_samples = min_samples
        self._audit = audit_logger
        self._state = self._load_state()
        if store is not None:
            self.attach_store(store)

    def _load_state(self) -> Dict[str, Any]:
        return self._load_state_dict({
            "last_analysis": None,
            "total_proposals": 0,
            "proposals_applied": 0,
            "proposals_rejected": 0,
            "patterns_discovered": 0,
            "current_prior_adjustments": {},
        })

    def _save_state(self) -> None:
        self._save_state_dict(self._state)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_outcome(
        self,
        hypothesis_id: str,
        hypothesis_source: str = "",
        hypothesis_expression: str = "",
        regime: str = "",
        backtest_sharpe: float = 0.0,
        backtest_max_dd: float = 0.0,
        backtest_win_rate: float = 0.0,
        backtest_n_trades: int = 0,
        live_sharpe: float = 0.0,
        live_pnl: float = 0.0,
        live_max_dd: float = 0.0,
        live_n_trades: int = 0,
        decay_status: str = "",
        ic_raw: float = 0.0,
        ic_purified: float = 0.0,
        feature_type: str = "",
        holding_horizon: str = "",
        alpha_weights: Optional[Dict[str, float]] = None,
    ) -> int:
        """Log a hypothesis outcome to C_meta.

        Call this after live results are available for a hypothesis.
        Returns the corpus entry ID.
        """
        entry = CorpusEntry(
            hypothesis_id=hypothesis_id,
            hypothesis_source=hypothesis_source,
            hypothesis_expression=hypothesis_expression,
            regime_at_entry=regime,
            backtest_sharpe=backtest_sharpe,
            backtest_max_dd=backtest_max_dd,
            backtest_win_rate=backtest_win_rate,
            backtest_n_trades=backtest_n_trades,
            live_sharpe=live_sharpe,
            live_pnl=live_pnl,
            live_max_dd=live_max_dd,
            live_n_trades=live_n_trades,
            decay_status=decay_status,
            ic_raw=ic_raw,
            ic_purified=ic_purified,
            feature_type=feature_type,
            holding_horizon=holding_horizon,
            alpha_weights_snapshot=alpha_weights or {},
        )
        entry_id = self.corpus.log_entry(entry)
        logger.info("Meta-loop logged outcome: %s (entry=%d)", hypothesis_id, entry_id)
        return entry_id

    def run_meta_analysis(
        self,
        current_alpha_weights: Optional[Dict[str, float]] = None,
        current_beta_thresholds: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Run the full meta-analysis cycle.

        This is the main entry point called periodically by auto_quant_tools.

        Steps:
        1. Check if enough data
        2. Mine patterns from C_meta
        3. Propose Δpriors based on patterns
        4. Shadow-deploy each proposal
        5. Apply or reject based on DSR gate

        Returns:
            Summary of the analysis cycle.
        """
        entry_count = self.corpus.get_entry_count()
        if entry_count < self.min_samples:
            return {
                "status": "INSUFFICIENT_DATA",
                "entries": entry_count,
                "min_required": self.min_samples,
            }

        # Step 1: Mine patterns
        patterns = self.miner.mine_all()
        for p in patterns:
            self.corpus.log_pattern(p)
        logger.info("Meta-loop mined %d patterns", len(patterns))

        # Step 2: Propose Δpriors
        proposals = self._generate_proposals(
            patterns,
            current_alpha_weights or {},
            current_beta_thresholds or {},
        )
        for p in proposals:
            self.corpus.log_proposal(p)

        # Step 3: Shadow-deploy each proposal
        entries = self.corpus.get_entries(limit=500)
        applied = []
        rejected = []

        for proposal in proposals:
            passed, dsr, reason = self.shadow.validate_proposal(
                proposal,
                current_alpha_weights or {},
                entries,
            )

            if passed:
                proposal.status = ProposalStatus.APPLIED
                proposal.applied_at = datetime.now(timezone.utc).isoformat()
                applied.append(proposal)
                self._state["proposals_applied"] = self._state.get("proposals_applied", 0) + 1
                logger.info("Meta-loop APPLIED proposal %s: %s", proposal.proposal_id, reason)
            else:
                proposal.status = ProposalStatus.REJECTED
                proposal.rejected_at = datetime.now(timezone.utc).isoformat()
                proposal.rejection_reason = reason
                rejected.append(proposal)
                self._state["proposals_rejected"] = self._state.get("proposals_rejected", 0) + 1
                logger.info("Meta-loop REJECTED proposal %s: %s", proposal.proposal_id, reason)

            # Log outcome to corpus
            self.corpus.log_proposal(proposal)

        # Update state
        self._state["last_analysis"] = datetime.now(timezone.utc).isoformat()
        self._state["total_proposals"] = self._state.get("total_proposals", 0) + len(proposals)
        self._state["patterns_discovered"] = self._state.get("patterns_discovered", 0) + len(patterns)
        self._save_state()

        # Compile applied changes
        applied_changes = {}
        for p in applied:
            applied_changes[p.prior_type.value] = p.proposed_changes

        return {
            "status": "COMPLETED",
            "entries_analyzed": entry_count,
            "patterns_found": len(patterns),
            "proposals_made": len(proposals),
            "proposals_applied": len(applied),
            "proposals_rejected": len(rejected),
            "applied_changes": applied_changes,
            "patterns": [p.to_dict() for p in patterns[:5]],
            "proposals": [p.to_dict() for p in proposals],
        }

    def _generate_proposals(
        self,
        patterns: List[Pattern],
        current_weights: Dict[str, float],
        current_thresholds: Dict[str, float],
    ) -> List[DeltaPriors]:
        """Generate Δpriors proposals from discovered patterns."""
        proposals = []
        proposal_counter = self._state.get("total_proposals", 0)

        for pattern in patterns:
            proposal_counter += 1
            pid = f"meta_{proposal_counter:04d}"

            if pattern.pattern_type == PatternType.REGIME_DECAY:
                # Propose: reduce weights for affected alphas
                changes = {}
                for hid in pattern.affected_items[:5]:
                    changes[hid] = 0.5  # halve weight for dead alphas
                proposals.append(DeltaPriors(
                    proposal_id=pid,
                    prior_type=PriorType.ALPHA_WEIGHTS,
                    proposed_changes=changes,
                    justification=f"Pattern: {pattern.description}",
                    pattern_ids=[pattern.description],
                ))

            elif pattern.pattern_type == PatternType.SOURCE_OVERFIT:
                # Propose: tighten β thresholds for this source
                changes = {
                    "sharpe_threshold": current_thresholds.get("sharpe_threshold", 0.5) * 1.2,
                    "overfit_tolerance": current_thresholds.get("overfit_tolerance", 0.3) * 0.8,
                    "affected_source": pattern.affected_items[0] if pattern.affected_items else "",
                }
                proposals.append(DeltaPriors(
                    proposal_id=pid,
                    prior_type=PriorType.BETA_THRESHOLDS,
                    proposed_changes=changes,
                    justification=f"Pattern: {pattern.description}",
                    pattern_ids=[pattern.description],
                ))

            elif pattern.pattern_type == PatternType.FEATURE_FRAGILE:
                # Propose: reduce sizing for fragile features
                changes = {"size_factor": 0.7}
                proposals.append(DeltaPriors(
                    proposal_id=pid,
                    prior_type=PriorType.RHO_SIZING,
                    proposed_changes=changes,
                    justification=f"Pattern: {pattern.description}",
                    pattern_ids=[pattern.description],
                ))

        return proposals

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        """Return summary of the meta-loop state."""
        return {
            "total_entries": self.corpus.get_entry_count(),
            "total_patterns": len(self.corpus.get_patterns()),
            "total_proposals": len(self.corpus.get_proposals()),
            "state": self._state,
        }

    def format_summary(self) -> str:
        """Format summary as human-readable string."""
        s = self.get_summary()
        lines = [
            "=" * 60,
            "META-LOOP (Loop 4) — SELF-EVOLUTION ENGINE",
            "=" * 60,
            f"Corpus entries: {s['total_entries']}",
            f"Patterns discovered: {s['total_patterns']}",
            f"Proposals made: {s['total_proposals']}",
            f"  Applied: {s['state'].get('proposals_applied', 0)}",
            f"  Rejected: {s['state'].get('proposals_rejected', 0)}",
            f"Last analysis: {s['state'].get('last_analysis', 'never')}",
            "=" * 60,
        ]

        # Show recent patterns
        patterns = self.corpus.get_patterns(limit=5)
        if patterns:
            lines.append("\nRECENT PATTERNS:")
            for p in patterns:
                lines.append(f"  [{p.pattern_type.value}] {p.description}")
                lines.append(f"    Confidence: {p.confidence:.2f}, Evidence: {p.evidence_count}")

        # Show recent proposals
        proposals = self.corpus.get_proposals(limit=5)
        if proposals:
            lines.append("\nRECENT PROPOSALS:")
            for p in proposals:
                status_icon = "✅" if p.status == ProposalStatus.APPLIED else "❌"
                lines.append(f"  {status_icon} [{p.proposal_id}] {p.prior_type.value}")
                lines.append(f"    {p.justification[:80]}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_loop: Optional[MetaLoop] = None


def get_meta_loop(**kwargs: Any) -> MetaLoop:
    """Get or create the singleton Meta-Loop.

    Test isolation: set ``META_LOOP_DB`` in the environment to route the
    singleton at an isolated corpus DB.  Production must never set it —
    the default is the real meta_corpus.db.  (Closes the Sep-19 leak where
    sandbox verification seeded the production corpus through the
    singleton because no override point existed.)
    """
    global _loop
    if _loop is None:
        env_db = os.environ.get("META_LOOP_DB")
        if env_db and kwargs.get("db_path") is None:
            kwargs["db_path"] = env_db
        env_state = os.environ.get("META_LOOP_STATE")
        if env_state and kwargs.get("state_path") is None:
            kwargs["state_path"] = env_state
        _loop = MetaLoop(**kwargs)
    return _loop
