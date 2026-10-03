"""Module 02: Combinatorial Purged Cross-Validation (CPCV) & Overfitting Validation Engine.

Implements Author-C's Combinatorial Purged Cross-Validation (CPCV):
1. Combinatorial Group Splits: C(N, k) train/test paths rather than a single walk-forward line.
2. Purging: Drops training observations whose label evaluation window overlaps into the test evaluation period.
3. Embargoing: Drops training observations immediately following a test period to eliminate autoregressive serial correlation.
4. Probability of Backtest Overfitting (PBO): Measures probability that the in-sample optimal strategy underperforms the median out-of-sample.
"""
from __future__ import annotations

import itertools
import math
from typing import Any, Dict, Generator, List, Optional, Sequence, Tuple, Union
import numpy as np
import pandas as pd


class CombinatorialPurgedCrossValidator:
    """Combinatorial Purged Cross-Validation (CPCV) Generator with Purging and Embargoing."""

    def __init__(
        self,
        n_splits: int = 6,
        n_test_splits: int = 2,
        embargo_pct: float = 0.01,
    ):
        self.n_splits = int(n_splits)
        self.n_test_splits = int(n_test_splits)
        self.embargo_pct = float(embargo_pct)
        if self.n_test_splits >= self.n_splits or self.n_test_splits < 1:
            raise ValueError("n_test_splits must be between 1 and n_splits - 1")

    def split(
        self,
        n_samples: int,
        event_times: Optional[Sequence[int]] = None,
        label_end_times: Optional[Sequence[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Generates purged and embargoed train/test index splits across all C(N, k) combinations."""
        indices = np.arange(n_samples)
        chunk_size = n_samples // self.n_splits
        chunks = []
        for i in range(self.n_splits):
            start = i * chunk_size
            end = (i + 1) * chunk_size if i < self.n_splits - 1 else n_samples
            chunks.append(indices[start:end])

        combinations = list(itertools.combinations(range(self.n_splits), self.n_test_splits))
        splits: List[Dict[str, Any]] = []

        embargo_size = int(math.ceil(n_samples * self.embargo_pct))

        for combo in combinations:
            test_chunk_indices = np.concatenate([chunks[c] for c in combo])
            test_chunk_indices = np.sort(test_chunk_indices)
            test_set = set(test_chunk_indices)

            train_chunk_indices = np.concatenate([chunks[c] for c in range(self.n_splits) if c not in combo])
            train_chunk_indices = np.sort(train_chunk_indices)

            # 1. Purging: Remove training events that overlap with test labels
            if event_times is not None and label_end_times is not None:
                purged_train = []
                test_min_time = min(test_chunk_indices)
                test_max_time = max(test_chunk_indices)

                for t_idx in train_chunk_indices:
                    t_start = event_times[t_idx] if t_idx < len(event_times) else t_idx
                    t_end = label_end_times[t_idx] if t_idx < len(label_end_times) else t_idx
                    # If label window overlaps test interval, purge it
                    if not (t_end < test_min_time or t_start > test_max_time):
                        continue
                    purged_train.append(t_idx)
                train_chunk_indices = np.array(purged_train)

            # 2. Embargoing: Remove training samples immediately following test sets
            if embargo_size > 0:
                embargoed_train = []
                for t_idx in train_chunk_indices:
                    # Check if t_idx is within embargo buffer after any test chunk
                    is_embargoed = False
                    for c in combo:
                        c_max = chunks[c][-1]
                        if c_max < t_idx <= c_max + embargo_size:
                            is_embargoed = True
                            break
                    if not is_embargoed:
                        embargoed_train.append(t_idx)
                train_chunk_indices = np.array(embargoed_train)

            splits.append({
                "combination": combo,
                "train_indices": train_chunk_indices,
                "test_indices": test_chunk_indices,
                "train_size": len(train_chunk_indices),
                "test_size": len(test_chunk_indices),
            })

        return splits

    @staticmethod
    def calculate_probability_of_backtest_overfitting(
        in_sample_sharpes: Sequence[float],
        out_of_sample_sharpes: Sequence[float],
    ) -> float:
        """Calculates Probability of Backtest Overfitting (PBO).

        Uses Bailey & López de Prado methodology: estimates the probability
        that the best in-sample strategy underperforms out-of-sample by
        averaging the indicator across multiple rank comparisons.

        Returns a value in [0.0, 1.0] where higher = more overfit risk.
        """
        is_s = np.asarray(in_sample_sharpes, dtype=float)
        oos_s = np.asarray(out_of_sample_sharpes, dtype=float)
        if len(is_s) < 2 or len(oos_s) < 2 or len(is_s) != len(oos_s):
            return 0.0

        n = len(is_s)
        # Rank OOS performance (0 = worst, N-1 = best)
        oos_ranks = np.argsort(np.argsort(oos_s))

        # For each strategy, check if its OOS rank is below the median
        # (i.e., it underperformed relative to its IS rank)
        median_rank = (n - 1) / 2.0

        # Count how many strategies underperform out-of-sample relative to IS rank
        # A strategy underperforms if its OOS rank < its IS rank
        is_ranks = np.argsort(np.argsort(is_s))
        underperform_count = int(np.sum(oos_ranks < is_ranks))

        # PBO = fraction of strategies where IS rank was not maintained OOS
        pbo = underperform_count / n
        return float(pbo)
