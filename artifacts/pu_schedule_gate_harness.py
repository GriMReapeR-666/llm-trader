"""PU-0161 schedule-gate harness: verify BOTH boot chassis yield on a
mismatched arm day, quickly, WITHOUT spawning a trader.

Strategy: force today to be the *other* arm's day by choosing the anchor,
import each chassis fresh, and call main(). A correct implementation returns 0
in milliseconds (skip fires before any slot wait). A regression would fall into
the multi-hour slot-wait loop, so every call runs under a hard timeout.

Markers: END-MARKER-PU-0161-GATE
"""
from __future__ import annotations

import importlib
import multiprocessing as mp
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "artifacts"))
sys.path.insert(0, ROOT)
for sub in ("core", "intelligence", "operations", "research", "data", "ui"):
    p = os.path.join(ROOT, sub)
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)

from pu_ablation_schedule import ARM_ABLATION, ARM_BASELINE, resolve  # noqa: E402

TIMEOUT_S = 25


def _run_main(module_name: str, arm: str, anchor: str, q: mp.Queue) -> None:
    """Child process: import the chassis and call main()."""
    os.environ["PU_ABLATION_SCHEDULE"] = "alternate"
    os.environ["PU_ABLATION_ARM"] = arm
    os.environ["PU_ABLATION_ANCHOR"] = anchor
    os.environ.pop("WATCH_S10_ABLATION", None)
    try:
        sys.path.insert(0, os.path.join(ROOT, "artifacts"))
        mod = importlib.import_module(module_name)
        importlib.reload(mod)
        q.put(("rc", mod.main()))
    except Exception as exc:  # pragma: no cover - diagnostic path
        q.put(("err", "{}: {}".format(type(exc).__name__, exc)))


def _check(label: str, module_name: str, arm: str, anchor: str) -> tuple[str, bool, str]:
    q: mp.Queue = mp.Queue()
    proc = mp.Process(target=_run_main, args=(module_name, arm, anchor, q))
    proc.start()
    proc.join(TIMEOUT_S)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        return label, False, "TIMED OUT after {}s - skip gate did not fire".format(TIMEOUT_S)
    if q.empty():
        return label, False, "no result (exit code {})".format(proc.exitcode)
    kind, val = q.get()
    if kind == "err":
        return label, False, "raised {}".format(val)
    # 0 == yielded for the day without booting; anything else is unexpected.
    return label, val == 0, "main() -> {}".format(val)


def main() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    print("today (UTC): {}".format(today))

    # Anchor = today        -> offset 0 -> ablation day -> session-11 must skip.
    # Anchor = today - 1    -> offset 1 -> baseline day  -> session-10 must skip.
    from datetime import timedelta
    d = datetime.now(timezone.utc).date()
    ablation_anchor = d.isoformat()                     # today is no_llm
    baseline_anchor = (d - timedelta(days=1)).isoformat()  # today is with_llm

    checks = [
        _check("session-10 (ablation arm) skips on BASELINE day",
               "pu_session10_boot", ARM_ABLATION, baseline_anchor),
        _check("session-11 (baseline arm) skips on ABLATION day",
               "pu_session11_boot", ARM_BASELINE, ablation_anchor),
    ]

    # Positive control: schedule DISABLED must NOT skip (returns from the
    # slot-wait loop only after the window, so we do not call main() here).
    # Instead assert the resolver's own verdict for both arms.
    os.environ["PU_ABLATION_SCHEDULE"] = ""
    off10 = resolve(arm_env=ARM_ABLATION, anchor_env=baseline_anchor)
    off11 = resolve(arm_env=ARM_BASELINE, anchor_env=ablation_anchor)
    checks.append(("disabled schedule: s10 does not self-skip",
                   off10.skip_reason == "", off10.skip_reason or "empty"))
    checks.append(("disabled schedule: s11 does not self-skip",
                   off11.skip_reason == "", off11.skip_reason or "empty"))

    failed = 0
    for label, ok, detail in checks:
        print("  [{}] {} ({})".format("PASS" if ok else "FAIL", label, detail))
        if not ok:
            failed += 1
    print("gate harness: {}/{} passed".format(len(checks) - failed, len(checks)))
    print("END-MARKER-PU-0161-GATE")
    return 1 if failed else 0


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    sys.exit(main())
