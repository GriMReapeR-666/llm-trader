"""QuantitativeCoach personas: self-improvement + monthly memory archiving.

Deterministic executors for two schedules:
  - self_improvement: consolidates operational lessons, runs optional RL, triggers meta-loop
  - monthly_memory_archive: archives old incidents and prunes stale memory

weekly_research is handled by research_agent.handle_sprint (strategy-factory sweeps).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from core.file_utils import safe_atomic_write_lines

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LESSONS_FILE = os.path.join(ROOT, "lessons.jsonl")
ARCHIVE_DIR = os.path.join(ROOT, "archive")
INCIDENTS_FILE = os.path.join(ROOT, "incidents.jsonl")

RL_ENV_FLAG = "TRADING_RL_ENABLE_UPDATES"


def _journal_path(context: Any) -> str:
    candidates = []
    if context is not None:
        store = getattr(context, "store", None)
        if store is not None:
            candidates.append(getattr(store, "path", ""))
        candidates.append(str(getattr(context, "journal_path", "")))
    candidates.append(os.path.join(ROOT, "trading_state.db"))
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return ""


def _recent_notables(db_path: str, limit: int = 200) -> List[Dict[str, Any]]:
    """WARN/ERROR operational events — the raw material for lessons."""
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT created_at, actor, event_type, severity, payload_json "
                "FROM operational_events "
                "WHERE severity IN ('WARNING','ERROR','CRITICAL') "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            out.append({
                "ts": r["created_at"],
                "actor": r["actor"],
                "kind": r["event_type"],
                "severity": r["severity"],
                "detail": (r["payload_json"] or "")[:160],
            })
        return out
    except Exception as exc:
        logger.debug("learning_agent read failed: %s", exc)
        return []


def _load_lessons() -> Dict[str, Dict[str, Any]]:
    lessons: Dict[str, Dict[str, Any]] = {}
    try:
        with open(LESSONS_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                lessons[rec["key"]] = rec
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("lessons read failed: %s", exc)
    return lessons


def consolidate_lessons(db_path: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Group notable events by kind; persist one rolling lesson per kind."""
    now = now or time.time()
    events = _recent_notables(db_path)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        grouped.setdefault(ev["kind"], []).append(ev)

    lessons = _load_lessons()
    new_or_updated = 0
    week_bucket = int(now // 604800)
    for kind, items in sorted(grouped.items()):
        key = f"{kind}"
        latest_ts = items[0]["ts"]
        prev = lessons.get(key)
        if prev is not None and prev.get("week_bucket") == week_bucket \
                and prev.get("count", 0) >= len(items):
            continue
        lessons[key] = {
            "key": key,
            "count": len(items),
            "last_seen": latest_ts,
            "week_bucket": week_bucket,
            "actors": sorted({i["actor"] for i in items})[:5],
            "sample": items[0]["detail"],
            "updated_ts": now,
        }
        new_or_updated += 1

    safe_atomic_write_lines(
        LESSONS_FILE,
        [json.dumps(rec) for rec in sorted(lessons.values(), key=lambda r: -int(r.get("updated_ts", 0)))],
    )

    if db_path and os.path.exists(db_path) and new_or_updated:
        _bridge_lessons_to_journal(lessons, db_path)

    return {"notable_events": len(events), "kinds": len(grouped),
            "lessons_upserted": new_or_updated}


def _bridge_lessons_to_journal(lessons: Dict[str, Dict[str, Any]], db_path: str) -> None:
    """Write consolidated lessons into TradingMemory verified_memories as CANDIDATE entries."""
    try:
        from intelligence.trading_memory import TradingMemory
        mem = TradingMemory(db_path)
        for kind, rec in lessons.items():
            source_id = f"lesson:{kind}:{rec.get('week_bucket', 0)}"
            if mem.is_verified_memory_source("operational_lesson", source_id):
                continue
            actors = ", ".join(rec.get("actors", [])[:5])
            summary = f"{kind} ({rec.get('count', 0)} events, actors: {actors})"
            lesson = rec.get("sample", "")[:500]
            mem.save_verified_memory(
                source_id=source_id,
                kind="operational_lesson",
                tags={"event_kind": kind, "week_bucket": str(rec.get("week_bucket", 0))},
                summary=summary,
                lesson=lesson,
                evidence={"count": rec.get("count", 0), "last_seen": rec.get("last_seen", "")},
                confidence=0.7,
                status="CANDIDATE",
            )
    except Exception as exc:
        logger.debug("bridge lessons to journal failed: %s", exc)


def maybe_run_rl_batch() -> Dict[str, Any]:
    """Offline RL is strictly opt-in and never grants order authority."""
    if os.environ.get(RL_ENV_FLAG, "").lower() not in ("1", "true", "yes"):
        return {"ran": False, "reason": f"{RL_ENV_FLAG} not enabled"}
    try:
        from research.rl_trainer import RLTrainer
        trainer = RLTrainer()
        result = trainer.train_on_kronos_simulator(episodes=5)
        return {"ran": True, "result": result}
    except ImportError:
        return {"ran": False, "reason": "rl_trainer unavailable"}
    except Exception as exc:
        return {"ran": False, "reason": f"rl batch failed: {exc}"}


# ---------- monthly memory archive ----------
ARCHIVE_AFTER_DAYS = 30


def _archive_old_incidents(now: float) -> int:
    """Move incident rows older than ARCHIVE_AFTER_DAYS into a monthly file."""
    if not os.path.exists(INCIDENTS_FILE):
        return 0
    kept: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []
    with open(INCIDENTS_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            (moved if now - float(rec.get("ts", 0)) > ARCHIVE_AFTER_DAYS * 86400
             else kept).append(rec)
    if moved:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        month_tag = datetime.fromtimestamp(now).strftime("%Y%m")
        archive_path = os.path.join(ARCHIVE_DIR, f"incidents-{month_tag}.jsonl")
        with open(archive_path, "a", encoding="utf-8") as fh:
            for rec in moved:
                fh.write(json.dumps(rec) + "\n")
        safe_atomic_write_lines(INCIDENTS_FILE, [json.dumps(rec) for rec in kept])
    return len(moved)


from datetime import datetime  # noqa: E402  (used by archive month tag)


def handle_self_improvement(payload: Dict[str, Any],
                            context: Any = None) -> Dict[str, Any]:
    from operations.event_bridge import emit

    db = _journal_path(context)
    lessons = consolidate_lessons(db)
    rl = maybe_run_rl_batch()

    meta_result: Dict[str, Any] = {}
    try:
        from core.meta_loop import get_meta_loop
        meta = get_meta_loop()
        meta_result = meta.run_meta_analysis()
    except Exception as exc:
        logger.debug("meta-loop trigger from self_improvement failed: %s", exc)

    improvement_result: Dict[str, Any] = {}
    try:
        from operations.improvement_pipeline import ImprovementPipeline
        pipeline = ImprovementPipeline(base_dir=ROOT)
        cycle_count = int((payload or {}).get("cycle_count", 0))
        improvement_result = pipeline.run_cycle(cycle_count=cycle_count)
    except Exception as exc:
        logger.debug("improvement pipeline trigger from self_improvement failed: %s", exc)

    result = {
        "lessons": lessons,
        "rl": rl,
        "meta_loop": meta_result,
        "improvement_pipeline": improvement_result,
    }
    emit("SELF_IMPROVEMENT", {"kinds": lessons.get("kinds", 0),
                              "upserted": lessons.get("lessons_upserted", 0),
                              "rl_ran": rl.get("ran", False),
                              "meta_loop_analysed": bool(meta_result),
                              "improvements_applied": len(improvement_result.get("improvements_applied", [])),
                              "best_signal": improvement_result.get("best_signal_selected")},
         actor="QuantitativeCoach")
    return result


def handle_memory_archive(payload: Dict[str, Any],
                          context: Any = None) -> Dict[str, Any]:
    from operations.event_bridge import emit

    moved = _archive_old_incidents(time.time())
    emit("MEMORY_ARCHIVE", {"archived_incidents": moved}, actor="QuantitativeCoach")
    return {"archived_incidents": moved}
