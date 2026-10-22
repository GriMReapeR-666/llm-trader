"""Artifacts must be portable: no absolute path may survive a write.

An artifact is evidence.  An evidence file that records
``C:\\Users\\Someone\\OneDrive\\Desktop\\...`` cannot be opened by the next
reader, cannot be reproduced on another machine, and publishes the operator's
folder structure to whoever reads it.

The contract has two halves and this module pins both:

* the WRITERS strip absolute paths, so a freshly written artifact is
  portable (pipeline results, the capstone result, the news export);
* the CHECKER finds any absolute path that slipped through, so a regression
  is visible instead of silent.

The rules live in ``core/artifact_paths.py`` — one module, so the writer, the
scrubber and this test cannot drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.artifact_paths import (
    find_absolute_paths,
    is_absolute_path,
    relative_path,
    sanitize_text,
    scan_value,
    scrub,
)

# A path that must never survive a write: absolute, machine-specific and
# carrying the operator's folder layout.
LEAK = r"C:\Users\Someone\OneDrive\Desktop\selfmade game projects\llm trader\trading_state.db"
POSIX_LEAK = "/home/someone/project/trading_state.db"


# ---------------------------------------------------------------------------
# The rule set
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [LEAK, POSIX_LEAK])
def test_absolute_paths_are_detected(text):
    assert is_absolute_path(text)
    assert find_absolute_paths(text)


@pytest.mark.parametrize("text", [
    "artifacts/capstone/x.json",
    "data/journals/EURUSD_2026.db",
    "https://example.com/a/b",
    "http://example.com",
    r"JSON:\n{",
    r"N:\n{",
    "C:\\",
    "no path at all",
])
def test_relative_paths_and_urls_are_left_alone(text):
    assert not is_absolute_path(text), text
    assert sanitize_text(text) == text


def test_sanitize_replaces_the_path_and_keeps_the_text():
    out = sanitize_text("journal %s closed" % LEAK)
    assert LEAK not in out
    assert "closed" in out
    assert not is_absolute_path(out)


def test_sanitize_is_idempotent():
    once = sanitize_text("journal %s" % LEAK)
    assert sanitize_text(once) == once


def test_scrub_walks_a_json_structure():
    payload = {"stages": {"walkforward": {"journal": LEAK}}, "n": 3, "ok": True}
    cleaned = scrub(payload)
    assert cleaned["stages"]["walkforward"]["journal"] != LEAK
    assert cleaned["n"] == 3 and cleaned["ok"] is True


def test_scan_value_reports_the_json_path():
    payload = {"a": [{"b": LEAK}]}
    hits = scan_value(payload)
    assert hits and hits[0][0] == "$.a[0].b"


def test_relative_path_of_a_file_inside_the_repo():
    root = Path(__file__).resolve().parent.parent
    assert relative_path(str(root / "trading_state.db")) == "trading_state.db"


def test_relative_path_of_a_file_outside_the_repo_keeps_only_the_name():
    assert relative_path(LEAK) == "trading_state.db"


# ---------------------------------------------------------------------------
# The writers
# ---------------------------------------------------------------------------

def test_pipeline_results_are_written_without_absolute_paths(tmp_path):
    from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

    pipeline = ContinuousBacktestPipeline(base_dir=str(tmp_path))
    pipeline._save_results({
        "symbol": "EURUSD",
        "stages": {
            "walkforward": {"status": "OK", "journal": LEAK},
            "backtest": {"status": "FAILED", "error": "boom in %s" % LEAK},
        },
    })
    written = Path(pipeline.results_path).read_text(encoding="utf-8")
    assert LEAK not in written
    data = json.loads(written)
    assert not scan_value(data)


def test_pipeline_state_is_written_without_absolute_paths(tmp_path):
    from research.continuous_backtest_pipeline import ContinuousBacktestPipeline

    pipeline = ContinuousBacktestPipeline(base_dir=str(tmp_path))
    pipeline.state["last_journal"] = LEAK
    pipeline._save_state()
    written = Path(pipeline.state_path).read_text(encoding="utf-8")
    assert LEAK not in written


def test_capstone_result_is_written_without_absolute_paths(tmp_path):
    from core.capstone_validator import CapstoneResult, CapstoneValidator, ValidationPhase

    validator = CapstoneValidator(output_dir=str(tmp_path))
    result = CapstoneResult(
        symbol="EURUSD", timeframe="H1", total_trades=3,
        phases=[ValidationPhase("journal_gathering", "ERROR", 0, 0.1,
                                {"error": "cannot import (C:\\Users\\me\\x.py)"})],
        benchmark_score=10.0, benchmark_grade="C", ready_for_live=False,
        gate_passed=False, summary="s", artifacts={"trades": LEAK},
    )
    out_file = validator._write_result(result, "EURUSD")
    written = out_file.read_text(encoding="utf-8")
    assert LEAK not in written
    assert not scan_value(json.loads(written))


def test_capstone_side_artifacts_are_written_without_absolute_paths(tmp_path):
    from core.capstone_validator import CapstoneValidator

    validator = CapstoneValidator(output_dir=str(tmp_path))
    validator._all_trades = [{"ticket": 1, "note": LEAK}]
    validator._scenario_results = {"scenario": LEAK}
    artifacts = validator._save_artifacts(None, "EURUSD")
    for key in ("trades", "scenarios"):
        written = Path(artifacts[key]).read_text(encoding="utf-8")
        assert LEAK not in written


def test_news_export_is_written_without_absolute_paths(tmp_path):
    from core.news_regime_correlation import NewsRegimeCorrelation

    db_path = tmp_path / "news.db"
    engine = NewsRegimeCorrelation(db_path=str(db_path))
    out = engine.export_json(output_path=str(tmp_path / "export.json"))
    written = Path(out).read_text(encoding="utf-8")
    assert str(db_path) not in written
    assert not scan_value(json.loads(written))


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------

def test_scrub_tree_rewrites_the_files_that_leak(tmp_path):
    """The tree scrubber is the operator's repair tool for old artifacts.

    It has to find a path in the DECODED value (the raw text of a JSON file
    escapes every backslash, so "C:\\Users\\..." does not look like a path
    until it is parsed) and rewrite the file.
    """
    from core.artifact_paths import scrub_tree

    folder = tmp_path / "artifacts"
    folder.mkdir()
    leaking = folder / "leaky.json"
    leaking.write_text(json.dumps({"journal": LEAK}), encoding="utf-8")
    clean = folder / "clean.json"
    clean.write_text(json.dumps({"journal": "trading_state.db"}), encoding="utf-8")

    report = scrub_tree(str(folder))
    assert report["changed"] == 1
    assert not scan_value(json.loads(leaking.read_text(encoding="utf-8")))
    # A clean file is left byte-identical.
    assert clean.read_text(encoding="utf-8") == json.dumps({"journal": "trading_state.db"})

