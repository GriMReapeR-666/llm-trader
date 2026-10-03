"""P5/P6 tests: learning executors, metrics exporter, secrets guard."""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations.agents import learning_agent  # noqa: E402
from operations.executors import REGISTRY, validate_registry  # noqa: E402
from operations.metrics_exporter import collect, write_metrics  # noqa: E402
from operations.secrets_guard import scan_file  # noqa: E402


def _seed_events_db(tmp_path, rows):
    db = str(tmp_path / "journal.db")
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE operational_events (created_at TEXT, actor TEXT,"
            " event_type TEXT, severity TEXT, payload_json TEXT)")
        for i, (actor, kind, sev) in enumerate(rows):
            con.execute("INSERT INTO operational_events VALUES (?,?,?,?,?)",
                        (f"2026-08-20T0{i}:00:00", actor, kind, sev,
                         json.dumps({"i": i})))
    return db


def test_self_improvement_distills_lessons_and_dedupes(tmp_path, monkeypatch):
    db = _seed_events_db(tmp_path, [
        ("RiskController", "skip_cooldown", "INFO"),
        ("RiskController", "slippage_breach", "WARNING"),
        ("RiskController", "slippage_breach", "WARNING"),
    ])
    lessons_file = str(tmp_path / "lessons.jsonl")
    monkeypatch.setattr(learning_agent, "LESSONS_FILE", lessons_file)
    monkeypatch.setattr(learning_agent, "_journal_path", lambda ctx: db)

    first = learning_agent.consolidate_lessons(db)
    assert first["notable_events"] == 2          # INFO excluded
    assert first["kinds"] == 1

    second = learning_agent.consolidate_lessons(db)
    assert second["lessons_upserted"] == 0       # same week, same count → dedupe

    with open(lessons_file, encoding="utf-8") as fh:
        recs = [json.loads(l) for l in fh]
    assert recs[0]["key"] == "slippage_breach" and recs[0]["count"] == 2


def test_rl_batch_stays_off_by_default(monkeypatch):
    monkeypatch.delenv("TRADING_RL_ENABLE_UPDATES", raising=False)
    res = learning_agent.maybe_run_rl_batch()
    assert res["ran"] is False and "not enabled" in res["reason"]


def test_monthly_archive_moves_old_incidents(tmp_path, monkeypatch):
    inc = str(tmp_path / "incidents.jsonl")
    old = {"ts": 1_600_000_000, "kind": "old", "severity": "warn"}
    new = {"ts": __import__("time").time(), "kind": "fresh", "severity": "critical"}
    with open(inc, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(old) + "\n" + json.dumps(new) + "\n")

    archive_dir = str(tmp_path / "archive")
    monkeypatch.setattr(learning_agent, "INCIDENTS_FILE", inc)
    monkeypatch.setattr(learning_agent, "ARCHIVE_DIR", archive_dir)

    moved = learning_agent._archive_old_incidents(__import__("time").time())
    assert moved == 1
    kept = [json.loads(l) for l in open(inc, encoding="utf-8")]
    assert len(kept) == 1 and kept[0]["kind"] == "fresh"
    archived_files = os.listdir(archive_dir)
    assert len(archived_files) == 1 and archived_files[0].startswith("incidents-")


def test_registry_claims_dead_schedules():
    validate_registry()
    for name in ("self_improvement", "weekly_research",
                 "monthly_memory_archive", "metrics_export"):
        assert name in REGISTRY


def test_metrics_export_prometheus_format(tmp_path):
    out = str(tmp_path / "trader.prom")
    res = write_metrics(out)
    assert res["ok"] is True
    content = open(out, encoding="utf-8").read()
    assert "# TYPE trader_halted gauge" in content
    assert "trader_events_emitted_total" in content
    for line in content.splitlines():
        if line and not line.startswith("#"):
            name_part = line.rsplit(" ", 1)
            float(name_part[1])              # every value numeric


def test_secrets_guard_detects_plaintext_password(tmp_path):
    f = str(tmp_path / "accounts.json")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump({"accounts": {"demo": {"login": 123, "password": "hunter2"}}}, fh)
    findings = scan_file(f)
    assert len(findings) == 1
    assert findings[0]["path"].endswith("password")
    assert "hu***" in findings[0]["hint"] or "hu" + "***" in findings[0]["hint"]
