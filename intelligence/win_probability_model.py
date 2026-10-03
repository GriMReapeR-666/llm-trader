"""Calibrated probability-of-win model served into position sizing.

Trains a compact L2 logistic regression on the durable journal reconciled,
closed trades: numeric setup features recorded with the proposal, label
WIN_* vs otherwise. A decile reliability map (empirical win rate per
predicted-probability bin) calibrates outputs so fractional-Kelly sizing
consumes an honest probability instead of the raw LLM confidence.

Never grants execution authority: output is a SIZING INPUT only. The caller
blends it conservatively (it can downsize, never upsize beyond raw trust).
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Canonical numeric features recorded with proposals. Each canonical key may
# arrive under any of its aliases (e.g. rsi_14 vs rsi); raw confidence is
# deliberately EXCLUDED: replacing it is the entire point of this model.
FEATURE_ALIASES = {
    "adx": ("adx", "adx_14"),
    "rsi": ("rsi_14", "rsi"),
    "ema_alignment": ("ema_alignment",),
    "mtf_alignment": ("mtf_alignment",),
    "atr_percentile": ("atr_percentile",),
    "hurst_exponent": ("hurst_exponent",),
    "spread_pips": ("spread_pips",),
}
CANONICAL_KEYS = tuple(FEATURE_ALIASES.keys())
MIN_PRESENT_FEATURES = 4   # rows with fewer known values are dropped
MIN_BIN_COUNT = 5


def _feature_vector(features: Any) -> Optional[Tuple[List[Optional[float]], int]]:
    """Alias-resolved vector plus the count of KNOWN values.

    Missing canonical keys become None and are median-imputed downstream;
    rows with too few known values are rejected outright rather than filled
    with guesses across the board.
    """
    if not isinstance(features, dict) or not features:
        return None
    vec: List[Optional[float]] = []
    known = 0
    for key in CANONICAL_KEYS:
        val = None
        for alias in FEATURE_ALIASES[key]:
            candidate = features.get(alias)
            if (isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
                    and math.isfinite(float(candidate))):
                val = float(candidate)
                break
        if val is None:
            vec.append(None)
        else:
            vec.append(val)
            known += 1
    if known < MIN_PRESENT_FEATURES:
        return None
    return vec, known


def _decode_features(payload_json: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    decision = payload.get("decision")
    features = payload.get("features")
    if isinstance(decision, dict):
        inner = decision.get("features")
        if isinstance(inner, dict):
            return inner
    return features if isinstance(features, dict) else None


class WinProbabilityModel:
    """Logistic P(win | setup features) with decile reliability calibration."""

    def __init__(self, weights: List[float], bias: float,
                 means: List[float], stds: List[float],
                 calib_edges: List[float], calib_rates: List[float],
                 sample_count: int, win_rate: float):
        self.weights = np.asarray(weights, dtype=float)
        self.bias = float(bias)
        self.means = np.asarray(means, dtype=float)
        self.stds = np.asarray(stds, dtype=float)
        self.calib_edges = np.asarray(calib_edges, dtype=float)
        self.calib_rates = np.asarray(calib_rates, dtype=float)
        self.sample_count = int(sample_count)
        self.train_win_rate = float(win_rate)
        self.impute_values: List[float] = [0.0] * len(CANONICAL_KEYS)

    # -- training ---------------------------------------------------------
    @classmethod
    def train_from_store(cls, db_path: str = "trading_state.db",
                         min_samples: int = 30,
                         limit: int = 500,
                         l2: float = 1e-3,
                         epochs: int = 400,
                         lr: float = 0.05) -> Optional["WinProbabilityModel"]:
        try:
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    """SELECT t.side AS side, l.label AS label, t.payload AS payload
                       FROM trade_labels l JOIN trades t ON t.ticket = l.ticket
                       WHERE t.closed_at IS NOT NULL AND l.label IS NOT NULL
                       ORDER BY t.closed_at DESC LIMIT ?""",
                    (int(limit),),
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return None

        xs: List[List[Optional[float]]] = []
        ys: List[int] = []
        for row in rows:
            label = str(row["label"] or "")
            if label.startswith("BREAKEVEN"):
                continue  # ambiguous outcome: excluded from both classes
            features = _decode_features(row["payload"])
            extracted = _feature_vector(features)
            if extracted is None:
                continue
            xs.append(extracted[0])
            ys.append(1 if label.startswith("WIN") else 0)
        if len(xs) < max(4, int(min_samples)) or len(set(ys)) < 2:
            return None

        # Median-impute unknown cells per canonical key.
        impute: List[float] = []
        for col in range(len(CANONICAL_KEYS)):
            known = [row[col] for row in xs if row[col] is not None]
            impute.append(float(np.median(known)) if known else 0.0)
        dense = [[float(v) if v is not None else impute[c]
                  for c, v in enumerate(row)] for row in xs]

        X = np.asarray(dense, dtype=float)
        y = np.asarray(ys, dtype=float)
        means = X.mean(axis=0)
        stds = X.std(axis=0)
        stds[stds < 1e-9] = 1.0
        Xs = (X - means) / stds

        w = np.zeros(Xs.shape[1], dtype=float)
        b = 0.0
        n = float(len(y))
        for _ in range(int(epochs)):
            z = Xs @ w + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))
            grad_w = (Xs.T @ (p - y)) / n + l2 * w
            grad_b = float(np.mean(p - y))
            w -= lr * grad_w
            b -= lr * grad_b

        with np.errstate(over="ignore"):
            train_p = 1.0 / (1.0 + np.exp(-np.clip(Xs @ w + b, -30.0, 30.0)))

        # Decile reliability calibration: empirical win rate per predicted bin.
        order = np.argsort(train_p)
        sorted_p = train_p[order]
        sorted_y = y[order]
        n_bins = max(2, min(10, len(y) // MIN_BIN_COUNT))
        edges: List[float] = []
        rates: List[float] = []
        for k in range(n_bins):
            lo = int(k * len(y) / n_bins)
            hi = int((k + 1) * len(y) / n_bins)
            seg_y = sorted_y[lo:hi]
            if len(seg_y) < MIN_BIN_COUNT:
                continue
            edges.append(float(sorted_p[max(lo, 0)]))
            rates.append(float(np.mean(seg_y)))
        if not edges:
            return None

        overall = float(np.mean(y))
        model = cls(w.tolist(), b, means.tolist(), stds.tolist(),
                    edges, rates, len(y), overall)
        model.impute_values = impute
        return model

    # -- inference --------------------------------------------------------
    def predict_calibrated(self, features: Any) -> Optional[float]:
        extracted = _feature_vector(features)
        if extracted is None:
            return None
        vec, _known = extracted
        dense = [float(v) if v is not None else self.impute_values[c]
                 for c, v in enumerate(vec)]
        x = (np.asarray(dense, dtype=float) - self.means) / self.stds
        z = float(np.clip(np.dot(x, self.weights) + self.bias, -30.0, 30.0))
        p_raw = 1.0 / (1.0 + math.exp(-z))
        idx = int(np.searchsorted(self.calib_edges, p_raw, side="right") - 1)
        idx = max(0, min(idx, len(self.calib_rates) - 1))
        p_cal = float(self.calib_rates[idx])
        return max(0.01, min(0.99, p_cal))
