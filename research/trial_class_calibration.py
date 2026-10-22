"""Calibration table for the S.Xi multiple-testing bar -- global family vs classes.

The trial ledger deflates a new hypothesis against E[max|null] of the family
it was selected from.  With one undifferentiated family (2818 trials in the
live ledger) that bar is 3.53 SE: a real annualized Sharpe of 1.9 is
detected ~1% of the time, so the gate is correct and useless at the same
time (audit 2026-10-20, §7).  The lever the audit names is SCOPING -- and
``core.hypothesis_class`` now decides which family a hypothesis belongs to.

This module renders that lever as a table, in the two currencies the audit
used:

  * the **luck bar** -- E[max of k standard normals], i.e. how many standard
    errors above zero the best of k null trials reaches, and the annualized
    Sharpe that corresponds to at a reference sample size;
  * the **required Sharpe** -- the annualized Sharpe a strategy needs before
    the DSR confidence reaches the gate's tau (0.95);
  * the **power** -- measured by Monte Carlo, the audit's method: draw k
    null strategies, let one of them carry a real edge, take the best, and
    ask the gate.  That is exactly the selection process the deflation is
    supposed to undo.

Run it:

    python -m research.trial_class_calibration

It reads the live ledger, writes ``artifacts/trial_ledger/calibration_by_class.json``
and prints the markdown table.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.hypothesis_class import CLASSIFIER_VERSION, UNCLASSIFIED, describe_class
from core.metrics import (
    _expected_max_standard_normal,
    calculate_deflated_sharpe_ratio,
    expected_max_sharpe,
)

PERIODS_PER_YEAR = 252.0
GATE_TAU = 0.95          # DSR confidence the gate demands
ALPHA = 0.05             # the complementary p-value the gate rejects above
DEFAULT_LEDGER = os.path.join("artifacts", "trial_ledger.db")
DEFAULT_ARTIFACT = os.path.join("artifacts", "trial_ledger", "calibration_by_class.json")

# Annualized Sharpes the power column is measured at: the one the audit
# quotes (1.9) and two stronger edges, where the difference between a
# 2818-trial family and a scoped class actually shows up in the detection
# rate (at 1.9 the null maximum already sits at the edge's own level, so
# no family size can rescue it).
DEFAULT_TRUE_SHARPES = (1.90, 2.50, 3.00)


# ---------------------------------------------------------------------------
# The deterministic half of the table
# ---------------------------------------------------------------------------
def required_sharpe(
    n_trials: int,
    n_observations: int,
    tau: float = GATE_TAU,
    periods_per_year: float = PERIODS_PER_YEAR,
) -> float:
    """Annualized Sharpe needed for the DSR confidence to reach ``tau``.

    The DSR p-value is monotone in the Sharpe, so a bisection is exact
    enough (and cannot diverge).
    """
    target_p = 1.0 - tau
    lo, hi = 0.0, 100.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        p = calculate_deflated_sharpe_ratio(
            estimated_sharpe=mid,
            num_trials=max(1, n_trials),
            sample_size=max(10, n_observations),
            skewness=0.0,
            kurtosis=3.0,
            annualized=True,
            periods_per_year=periods_per_year,
        )
        if p > target_p:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 4)


def luck_bar(
    n_trials: int,
    n_observations: int,
    periods_per_year: float = PERIODS_PER_YEAR,
) -> Dict[str, float]:
    """The multiple-testing bar in SE units and in annualized Sharpe."""
    se_factor = _expected_max_standard_normal(max(1, int(n_trials)))
    bar = expected_max_sharpe(
        num_trials=max(1, int(n_trials)),
        sample_size=max(10, n_observations),
        annualized=True,
        periods_per_year=periods_per_year,
    )
    return {
        "luck_bar_se": round(se_factor, 4),
        "luck_bar_annualized": round(float(bar), 4),
    }


# ---------------------------------------------------------------------------
# The measured half of the table (Monte Carlo, the audit's method)
# ---------------------------------------------------------------------------
def detection_rate(
    n_trials: int,
    n_observations: int,
    true_sharpe_annualized: float,
    reps: int = 200,
    seed: int = 20261022,
    periods_per_year: float = PERIODS_PER_YEAR,
) -> float:
    """Probability the DSR gate detects a real edge after searching ``n_trials``.

    Draw ``n_trials`` independent null strategies of ``n_observations`` iid
    normal per-period returns, give ONE of them the real edge, take the
    best (that is the selection the deflation undoes), annualize, and ask
    the gate.
    """
    k = max(1, int(n_trials))
    n = max(10, int(n_observations))
    true_sr = float(true_sharpe_annualized) / math.sqrt(periods_per_year)
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(reps):
        returns = rng.standard_normal((k, n))
        returns[0] += true_sr
        sharpes = returns.mean(axis=1) / returns.std(axis=1)
        best = float(sharpes.max())
        p = calculate_deflated_sharpe_ratio(
            best * math.sqrt(periods_per_year),
            num_trials=k,
            sample_size=n,
            skewness=0.0,
            kurtosis=3.0,
            annualized=True,
            periods_per_year=periods_per_year,
        )
        if p < ALPHA:
            hits += 1
    return round(hits / reps, 4)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
def build_calibration_table(
    db_path: str = DEFAULT_LEDGER,
    n_observations: int = 500,
    true_sharpes: Sequence[float] = DEFAULT_TRUE_SHARPES,
    reps: int = 200,
    seed: int = 20261022,
    periods_per_year: float = PERIODS_PER_YEAR,
    measure_power: bool = True,
) -> Dict[str, Any]:
    """Build the per-class calibration table from the live ledger.

    The global family is the first row: that is the bar the ledger used
    before classes existed, and every class row is measured against it.

    The unattributed bucket (``general``) is NOT a row: an identity that
    cannot be attributed to a family keeps the GLOBAL bar (fail-closed),
    so publishing a bar for it would suggest a scope the gate does not
    offer.  Its size is reported on the global row instead.
    """
    from core.trial_ledger import TrialLedger

    ledger = TrialLedger(db_path=db_path)
    total = ledger.n_trials
    classes = ledger.list_strategy_classes()
    unattributed = int(classes.pop(UNCLASSIFIED, 0))

    rows: List[Dict[str, Any]] = []
    global_row = {
        "class_id": "global",
        "description": "Every trial the ledger has ever recorded (one undifferentiated family)",
        "n_trials": total,
        "unattributed_trials": unattributed,
    }
    global_row.update(luck_bar(total, n_observations, periods_per_year))
    global_row["required_sharpe"] = required_sharpe(
        total, n_observations, periods_per_year=periods_per_year)
    if measure_power:
        global_row["power"] = {
            f"{sr:.2f}": detection_rate(total, n_observations, sr, reps=reps, seed=seed,
                                        periods_per_year=periods_per_year)
            for sr in true_sharpes
        }
    rows.append(global_row)

    for class_id, count in classes.items():
        row: Dict[str, Any] = {
            "class_id": class_id,
            "description": describe_class(class_id),
            "n_trials": count,
        }
        row.update(luck_bar(count, n_observations, periods_per_year))
        row["required_sharpe"] = required_sharpe(
            count, n_observations, periods_per_year=periods_per_year)
        if measure_power:
            row["power"] = {
                f"{sr:.2f}": detection_rate(count, n_observations, sr, reps=reps, seed=seed,
                                            periods_per_year=periods_per_year)
                for sr in true_sharpes
            }
        rows.append(row)

    for row in rows:
        row["shift_vs_global"] = {
            "luck_bar_se": round(global_row["luck_bar_se"] - row["luck_bar_se"], 4),
            "required_sharpe": round(global_row["required_sharpe"] - row["required_sharpe"], 4),
        }

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "classifier_version": CLASSIFIER_VERSION,
        "ledger": {"path": os.path.abspath(db_path), "n_trials": total},
        "reference": {
            "n_observations": n_observations,
            "periods_per_year": periods_per_year,
            "gate_tau": GATE_TAU,
            "alpha": ALPHA,
            "true_sharpes_annualized": list(true_sharpes),
            "monte_carlo_reps": reps,
            "seed": seed,
        },
        "rows": rows,
    }


def render_markdown(table: Dict[str, Any]) -> str:
    """Render the table as markdown (the artifact's human-readable face).

    One power column per MEASURED Sharpe, so the printed table is exactly
    the artifact's power dict -- the doc quotes the SR 2.50 column, and a
    reader who regenerates the artifact must be able to find it here.
    """
    rows = table["rows"]
    ref = table["reference"]
    sharpes = [f"{float(sr):.2f}" for sr in ref.get("true_sharpes_annualized", ())]
    lines: List[str] = []
    lines.append("| family | trials (k) | luck bar (SE) | required SR | "
                 + " | ".join(f"power @ SR {sr}" for sr in sharpes)
                 + " | shift vs global (SR) |")
    lines.append("|---|---:|---:|---:|" + "---:|" * len(sharpes) + "---:|")
    for row in rows:
        power = row.get("power", {})
        cells = [f"{power.get(sr, 0.0):.0%}" if power else "—" for sr in sharpes]
        lines.append(
            "| `{family}` | {k} | {bar:.2f} | {req:.2f} | {power} | {shift:+.2f} |".format(
                family=row["class_id"],
                k=row["n_trials"],
                bar=row["luck_bar_se"],
                req=row["required_sharpe"],
                power=" | ".join(cells),
                shift=row["shift_vs_global"]["required_sharpe"],
            )
        )
    lines.append("")
    lines.append(
        "Reference: n={n} observations, {ppy:.0f} periods/year, gate tau={tau}, "
        "Monte Carlo reps={reps}, seed={seed}.".format(
            n=ref["n_observations"], ppy=ref["periods_per_year"], tau=ref["gate_tau"],
            reps=ref["monte_carlo_reps"], seed=ref["seed"],
        )
    )
    lines.append("")
    lines.append(
        "`global` is the bar the ledger used before hypothesis classes existed: one "
        "undifferentiated family of every trial ever recorded. Every other row is "
        "the same gate scoped to that research programme."
    )
    unattributed = rows[0].get("unattributed_trials") if rows else 0
    if unattributed:
        lines.append("")
        lines.append(
            f"{unattributed} trial(s) cannot be attributed to a family; they keep the "
            "`global` bar (fail-closed) and are not a scope of their own."
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="ledger DB path")
    parser.add_argument("--artifact", default=DEFAULT_ARTIFACT, help="JSON output path")
    parser.add_argument("--n", type=int, default=500, help="reference observations")
    parser.add_argument("--reps", type=int, default=200, help="Monte Carlo reps")
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--no-power", action="store_true", help="skip the Monte Carlo")
    args = parser.parse_args(argv)

    table = build_calibration_table(
        db_path=args.ledger,
        n_observations=args.n,
        reps=args.reps,
        seed=args.seed,
        measure_power=not args.no_power,
    )
    os.makedirs(os.path.dirname(os.path.abspath(args.artifact)), exist_ok=True)
    with open(args.artifact, "w", encoding="utf-8") as handle:
        json.dump(table, handle, indent=2)
    print(render_markdown(table))
    print(f"\nartifact: {args.artifact}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
