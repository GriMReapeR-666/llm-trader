"""Throwaway probe (artifacts/): per-step timing of event_monitor's network path.

Runs each step the 60s-budget handler performs, isolated, on a throwaway
macro-graph DB, with paper-mode flags mirroring the live session:
  1. collect_calendar_event_rows(None, None)          (configured calendar path)
  2. manager.get_fred_data(catalog_fred_series())     (FRED HTTP)
  3. manager.get_hot_topics(limit=5)                  (HTTP)
  4. manager.get_news(keywords, page_size=5)          (HTTP)
  5. process_cycle_data_sources(...)                  (graph ingest; optional providers OFF)
  6. run_realtime_news_ingest(..., include_rss=True)  (RSS HTTP — prime suspect)
Output: timing table + artifacts/probe_event_monitor_steps.json. MT5-terminal
calendar fetch is NOT measured (local terminal call; no MT5 on this box).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "data"), str(ROOT / "intelligence")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("FAST_TEST", "")  # do not disable live fetch paths
os.environ["TRADING_REALTIME_NEWS_EXTRACT"] = "false"  # live default: no LLM claims

SYMBOL = "EURUSD"
KEYWORDS = ["EURUSD", "EUR", "USD", "ECB", "Federal Reserve"]

results = []


def step(name, fn):
    t0 = time.perf_counter()
    status = "OK"
    detail = ""
    try:
        out = fn()
        detail = str(out)[:180]
    except Exception as exc:  # noqa: BLE001 - probe must record and continue
        status = "ERROR"
        detail = f"{type(exc).__name__}: {exc}"[:180]
    dt = time.perf_counter() - t0
    results.append({"step": name, "seconds": round(dt, 2), "status": status,
                    "detail": detail})
    print(f"[{dt:7.2f}s] {status:5} {name} :: {detail}", flush=True)
    return dt


def main() -> int:
    print("=== event_monitor isolated step probe ===", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="em_probe_"))
    from macro_graph import MacroGraph
    graph = MacroGraph(str(tmp / "graph.db"))
    from data.api_manager import APIManager
    manager = APIManager(store=None)
    print(f"tmp_dir={tmp} newsapi_key={'set' if manager.newsapi_key else 'MISSING'}"
          f" fred_key={'set' if manager.fred_key else 'MISSING'}", flush=True)

    # 1. calendar collector (configured path; MT5 not available on this box)
    from data.market_data_provider import collect_calendar_event_rows
    step("1 collect_calendar_event_rows(None,None)",
         lambda: collect_calendar_event_rows(None, None))

    from data_source_pipeline import catalog_fred_series, process_cycle_data_sources

    # 2-4. manager fetches (the handler runs these in nested try/except)
    step("2 get_fred_data(catalog)",
         lambda: manager.get_fred_data(catalog_fred_series()))
    step("3 get_hot_topics(5)",
         lambda: manager.get_hot_topics(limit=5))
    step("4 get_news(keywords, 5)",
         lambda: manager.get_news(KEYWORDS, page_size=5))

    fred_rows = {}
    try:
        fred_rows = manager.get_fred_data(catalog_fred_series()) or {}
    except Exception:
        pass
    api_news = []
    try:
        api_news = manager.get_news(KEYWORDS, page_size=5) or []
    except Exception:
        pass
    hot = []
    try:
        hot = manager.get_hot_topics(limit=5) or []
    except Exception:
        pass
    events = []
    try:
        events, _src = collect_calendar_event_rows(None, None)
    except Exception:
        pass

    # 5. pipeline ingest into throwaway graph (paper flags: no optional providers)
    step("5 process_cycle_data_sources(paper)",
         lambda: process_cycle_data_sources(
             macro_graph=graph,
             journal_path=str(tmp / "journal.db"),
             symbol=SYMBOL,
             calendar_events=events,
             api_news=api_news,
             hot_topics=hot,
             macro_data=fred_rows,
             api_manager=manager,
             include_optional_providers=False,
         ))

    # 6. realtime news ingest WITH RSS (live/paper-handler parity: include_rss=not paper)
    from realtime_news_ingest import run as run_realtime_news_ingest
    step("6 run_realtime_news_ingest(include_rss=True)",
         lambda: run_realtime_news_ingest(
             graph=graph,
             api_manager=manager,
             memory=None,
             llm=None,
             symbol=SYMBOL,
             keywords=KEYWORDS,
             include_rss=True,
             extract_claims=False,
         ))

    total = round(sum(r["seconds"] for r in results), 2)
    out = {
        "probe": "event_monitor_steps",
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "total_seconds": total,
        "budget_s": 60.0,
        "steps": results,
        "note": "throwaway graph DB; MT5-terminal calendar path not measured",
    }
    (ROOT / "artifacts" / "probe_event_monitor_steps.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nTOTAL={total}s vs budget=60s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
