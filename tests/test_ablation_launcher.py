"""Tests for artifacts/pu_ablation_launcher.py (Task 2)."""
from __future__ import annotations

import os
from datetime import date
import pytest

from artifacts.pu_ablation_launcher import (
    get_today_launcher_config,
    generate_paired_report,
    extract_latest_session_meta,
)


def test_ablation_launcher_schedule_resolution():
    cfg = get_today_launcher_config(today=date(2026, 10, 2))
    assert cfg["arm"] == "no_llm"
    assert "PU_ABLATION" in cfg["env"]

    cfg_base = get_today_launcher_config(today=date(2026, 10, 3))
    assert cfg_base["arm"] == "with_llm"
    assert "PU_ABLATION" not in cfg_base["env"]


def test_pair_report_generation(tmp_path):
    report_file = tmp_path / "pair_report.md"
    generate_paired_report(
        ablation_meta={
            "arm": "no_llm",
            "decisions": {"HOLD": 19, "BUY": 0, "SELL": 0},
            "requests": 0,
            "session": 10,
            "window": "07:55:32 -> 13:22 UTC",
        },
        baseline_meta={
            "arm": "with_llm",
            "decisions": {"HOLD": 3, "BUY": 14, "SELL": 1},
            "requests": 15,
            "session": 11,
            "window": "17:17:42 -> 22:16 UTC",
        },
        output_path=str(report_file),
    )
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Paired Ablation Analysis" in content
    assert "19 HOLD" in content
    assert "14 BUY" in content


def test_extract_latest_session_meta_fallback():
    # When no records match, it provides graceful empty fallback
    meta = extract_latest_session_meta(db_path=":memory:")
    assert "decisions" in meta
    assert meta["requests"] == 0
