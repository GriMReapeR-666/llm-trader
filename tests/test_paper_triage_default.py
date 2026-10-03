"""Paper fast-boot default: NEWS_TRIAGE_MODE=off unless explicitly overridden.

Auto-mode CPU Laya triage can silently spend ~25 minutes loading the model
checkpoint at boot (payload-audit session 2 masqueraded as a hang). Paper
launches now default to deterministic rule triage; an explicit env value
(shell, .env, or a launcher passthrough like START_DECK's TRIAGE_MODE=full)
always wins.
"""
import os

import pytest

from mt5_trader import apply_paper_triage_default
from data.news_triage import triage_mode


@pytest.fixture(autouse=True)
def _isolate_env():
    # monkeypatch.delenv(raising=False) records NO undo when the var is
    # absent, so apply_paper_triage_default()'s direct os.environ write
    # leaked NEWS_TRIAGE_MODE=off into every later test of the run (broke
    # triage_model_path/triage_ledger in full-suite order). Restore manually.
    had = "NEWS_TRIAGE_MODE" in os.environ
    old = os.environ.get("NEWS_TRIAGE_MODE")
    os.environ.pop("NEWS_TRIAGE_MODE", None)
    yield
    if had:
        os.environ["NEWS_TRIAGE_MODE"] = old
    else:
        os.environ.pop("NEWS_TRIAGE_MODE", None)


def test_paper_default_applies_off_and_is_visible_to_triage_mode():
    assert apply_paper_triage_default() is True
    assert os.environ["NEWS_TRIAGE_MODE"] == "off"
    # The triage layer must actually see the default (no caching of the env
    # read): _get_laya() bails out before any model load in off mode.
    assert triage_mode() == "off"


def test_explicit_env_value_is_respected(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "laya")
    assert apply_paper_triage_default() is False
    assert os.environ["NEWS_TRIAGE_MODE"] == "laya"
    assert triage_mode() == "laya"


def test_explicit_auto_is_respected(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "auto")
    assert apply_paper_triage_default() is False
    assert triage_mode() == "auto"


def test_explicit_off_is_respected(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "off")
    assert apply_paper_triage_default() is False
    assert triage_mode() == "off"


def test_whitespace_only_value_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("NEWS_TRIAGE_MODE", "   ")
    assert apply_paper_triage_default() is True
    assert os.environ["NEWS_TRIAGE_MODE"] == "off"
