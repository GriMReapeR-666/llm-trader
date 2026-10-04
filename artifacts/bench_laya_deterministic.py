"""Throwaway benchmark (artifacts/): deterministic news-triage latency.

Step A of the Laya-vs-deterministic comparison. Runs the deterministic
labeler path (triage_article with the laya=False force-off sentinel) over
synthetic reuters-style articles and writes its JSON artifact immediately,
so step B (the real-checkpoint subprocess benchmark) cannot lose these
numbers even if torch crashes natively under memory pressure.
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

N = 400

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
    arts = [article(i) for i in range(N)]
    seen: set = set()
    per_ms = []
    actions = {}
    t_all = time.perf_counter()
    for a in arts:
        t0 = time.perf_counter()
        v = news_triage.triage_article(a, seen_keys=seen, laya=False)
        per_ms.append((time.perf_counter() - t0) * 1000)
        actions[v.action] = actions.get(v.action, 0) + 1
    wall_ms = (time.perf_counter() - t_all) * 1000
    per_ms.sort()
    out = {
        "step": "A_deterministic",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "articles": N,
        "wall_ms": round(wall_ms, 1),
        "per_article_ms_mean": round(statistics.fmean(per_ms), 3),
        "per_article_ms_p50": round(per_ms[len(per_ms) // 2], 3),
        "per_article_ms_p95": round(per_ms[int(len(per_ms) * 0.95)], 3),
        "actions": actions,
        "note": "deterministic labeler path, laya=False force-off sentinel",
    }
    path = ROOT / "artifacts" / "bench_triage_stepA.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
