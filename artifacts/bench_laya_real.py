"""Throwaway benchmark step B (artifacts/): load the REAL Laya checkpoint in
this isolated process and time per-article decide() triage passes.

Run ONLY with explicit intent: needs ~1.8GB free RAM; a torch allocation
failure or native crash here is the honest fail-closed record (step A's
deterministic numbers are already saved by bench_laya_deterministic.py).
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "data"), str(ROOT / "intelligence")):
    if p not in sys.path:
        sys.path.insert(0, p)

import news_triage  # noqa: E402

TEMPLATES = [
    ("Fed holds rates steady at 4.25 percent, signals patience",
     "The Federal Reserve kept its benchmark rate unchanged and signaled patience on further moves."),
    ("ECB's Lagarde says inflation path remains bumpy",
     "European Central Bank President Christine Lagarde said the disinflation process remains bumpy."),
    ("US CPI rises 0.3 percent in August, hotter than forecast",
     "Consumer prices rose 0.3 percent last month, exceeding economist forecasts."),
    ("Oil extends gains as supply concerns persist",
     "Crude extended its rally amid persistent supply risks in the Red Sea corridor."),
    ("Analysts debate whether the rally can continue",
     "Some strategists think the rally has room to run; others call it overextended."),
    ("Treasuries slip as traders pare Fed cut bets",
     "Yields rose across the curve as traders trimmed expectations for rate cuts."),
]


def article(i: int) -> dict:
    title, summary = TEMPLATES[i % len(TEMPLATES)]
    return {
        "title": title,
        "text_or_summary": summary,
        "publisher": "Reuters" if i % 3 else "Bloomberg",
        "url": f"https://www.example.com/economy/story-{i}",
    }


def main() -> int:
    t_load0 = time.perf_counter()
    from research.decision_models.laya_model import get_laya_decision_model
    model = get_laya_decision_model("models/laya")
    load_s = time.perf_counter() - t_load0
    print(f"model loaded in {load_s:.2f}s", flush=True)

    arts = [article(i) for i in range(15)]
    seen: set = set()
    per_ms = []
    actions = {}
    laya_source = 0
    t_all = time.perf_counter()
    for a in arts:
        v = news_triage.triage_article(a, seen_keys=seen, laya=model)
        per_ms.append(v.latency_ms)
        if v.source == "laya":
            laya_source += 1
        actions[v.action] = actions.get(v.action, 0) + 1
    wall_ms = (time.perf_counter() - t_all) * 1000
    per_ms_sorted = sorted(per_ms)
    out = {
        "step": "B_laya",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "load_s": round(load_s, 2),
        "articles": len(arts),
        "laya_source_count": laya_source,
        "wall_ms": round(wall_ms, 1),
        "per_article_ms_mean": round(statistics.fmean(per_ms), 1),
        "per_article_ms_p50": round(per_ms_sorted[len(per_ms_sorted) // 2], 1),
        "per_article_ms_max": round(per_ms_sorted[-1], 1),
        "actions": actions,
        "note": "real checkpoint, CPU fp32, single forward pass per article",
    }
    path = ROOT / "artifacts" / "bench_triage_stepB.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
