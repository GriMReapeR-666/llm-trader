"""D9 kill-switch latch: confirmation, persistence, market-data clear, cooldown."""
import json
import os

import pytest

os.environ.setdefault("FAST_TEST", "1")

from kill_switch_latch import (
    DEFAULT_RELPATH,
    KillSwitchLatch,
    get_kill_switch_latch,
)


def _make(tmp_path, **kw):
    return KillSwitchLatch(path=str(tmp_path / "latch.json"), **kw)


# ---------------------------------------------------------------------------
# Confirmation ladder (news-only path)
# ---------------------------------------------------------------------------

def test_single_batch_does_not_latch(tmp_path):
    l = _make(tmp_path)
    s = l.observe(2, ["halt headline"])
    assert s["latched"] is False
    assert s["pending_confirmations"] == 1


def test_two_batches_within_window_latch(tmp_path):
    l = _make(tmp_path)
    l.observe(1, ["a"])
    s = l.observe(1, ["b"], now=l.snapshot()[""] if False else None)
    assert s["latched"] is True
    assert s["trigger_source"] == "news_tripwire_confirmed"
    assert "b" in s["triggered_headlines"]


def test_confirmation_window_expiry_resets(tmp_path):
    t = [1000.0]
    l = _make(tmp_path, now_fn=lambda: t[0], confirm_window_s=300.0)
    l.observe(1, ["a"])
    t[0] += 400.0  # first batch expired
    s = l.observe(1, ["b"])
    assert s["latched"] is False
    assert s["pending_confirmations"] == 1


def test_quiet_cycle_preserves_pending(tmp_path):
    l = _make(tmp_path)
    l.observe(1, ["a"])
    s = l.observe(0)  # quiet batch
    assert s["latched"] is False
    assert s["pending_confirmations"] == 1


# ---------------------------------------------------------------------------
# Persistence: latched survives cycles, silence, and process restart
# ---------------------------------------------------------------------------

def test_latched_persists_through_quiet_cycles(tmp_path):
    l = _make(tmp_path)
    l.observe(1, ["a"])
    l.observe(1, ["b"])
    s = l.observe(0)
    s = l.observe(0)
    assert s["latched"] is True


def test_latched_survives_restart(tmp_path):
    l1 = _make(tmp_path)
    l1.observe(1, ["a"])
    l1.observe(1, ["b"])
    l2 = _make(tmp_path)  # fresh instance, same checkpoint file
    assert l2.snapshot()["latched"] is True


def test_latch_checkpoint_is_atomic(tmp_path):
    l = _make(tmp_path)
    l.observe(1, ["a"])
    l.observe(1, ["b"])
    with open(tmp_path / "latch.json", "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["latched"] is True


# ---------------------------------------------------------------------------
# Market-data authority
# ---------------------------------------------------------------------------

def test_market_halt_engages_immediately_bypassing_confirmation(tmp_path):
    l = _make(tmp_path)
    s = l.apply_market_data(halt_flag=True)
    assert s["latched"] is True
    assert s["trigger_source"] == "market_data_halt"


def test_news_silence_never_clears(tmp_path):
    l = _make(tmp_path)
    l.apply_market_data(halt_flag=True)
    l.observe(0)
    l.observe(0)
    assert l.snapshot()["latched"] is True
    # apply_market_data with no market data at all is a no-op
    s = l.apply_market_data()
    assert s["latched"] is True


def test_missing_vix_does_not_clear(tmp_path):
    l = _make(tmp_path)
    l.apply_market_data(halt_flag=True)
    s = l.apply_market_data(halt_flag=False)  # halt absent but VIX unknown
    assert s["latched"] is True


def test_high_vix_blocks_clear(tmp_path):
    l = _make(tmp_path)
    l.apply_market_data(halt_flag=True)
    s = l.apply_market_data(vix=42.0)
    assert s["latched"] is True


def test_calm_market_explicitly_clears(tmp_path):
    l = _make(tmp_path)
    l.apply_market_data(halt_flag=True)
    s = l.apply_market_data(vix=15.0)
    assert s["latched"] is False
    assert s["clear_reason"] == "market_data_calm_vix_below_35"
    assert s["cleared_at"] is not None


def test_tripwire_during_cooldown_cannot_retrip(tmp_path):
    t = [1000.0]
    l = _make(tmp_path, now_fn=lambda: t[0])
    l.apply_market_data(halt_flag=True)
    s = l.apply_market_data(vix=15.0)  # clears, starts 900s cooldown
    assert s["latched"] is False
    assert s["cooldown_active"] is True
    t[0] += 100.0
    s = l.observe(2, ["flash halt"])
    assert s["latched"] is False
    assert s["suppressed_during_cooldown"] == 1
    s = l.observe(0)
    assert s["latched"] is False


def test_cooldown_expiry_re_arms(tmp_path):
    t = [1000.0]
    l = _make(tmp_path, now_fn=lambda: t[0])
    l.apply_market_data(halt_flag=True)
    l.apply_market_data(vix=15.0)
    t[0] += 1000.0  # cooldown expired
    l.observe(1, ["a"])
    s = l.observe(1, ["b"])
    assert s["latched"] is True
    assert s["suppressed_during_cooldown"] == 0


def test_market_halt_overrides_cooldown(tmp_path):
    t = [1000.0]
    l = _make(tmp_path, now_fn=lambda: t[0])
    l.apply_market_data(halt_flag=True)
    l.apply_market_data(vix=15.0)
    t[0] += 100.0  # still in cooldown
    s = l.apply_market_data(halt_flag=True)
    assert s["latched"] is True


# ---------------------------------------------------------------------------
# News-side semantics while latched
# ---------------------------------------------------------------------------

def test_news_cannot_unlatch(tmp_path):
    l = _make(tmp_path)
    l.apply_market_data(halt_flag=True)
    s = l.observe(0)
    assert s["latched"] is True


def test_news_updates_headlines_while_latched(tmp_path):
    l = _make(tmp_path)
    l.apply_market_data(halt_flag=True)
    s = l.observe(1, ["fresh halt headline"])
    assert s["latched"] is True
    assert s["triggered_headlines"] == ["fresh halt headline"]


# ---------------------------------------------------------------------------
# Cycle-path integration (run() observes, MacroAnalyst applies market data)
# ---------------------------------------------------------------------------

def test_get_kill_switch_latch_singleton():
    a = get_kill_switch_latch()
    b = get_kill_switch_latch()
    assert a is b


def test_run_result_uses_latch_state(tmp_path, monkeypatch):
    import data.realtime_news_ingest as rni
    from macro_graph import MacroGraph

    monkeypatch.setenv("D9_LATCH_PATH", str(tmp_path / "latch.json"))
    monkeypatch.setattr("news_triage._get_laya", lambda: None)
    monkeypatch.setattr(rni, "collect_newsapi_articles", lambda *a, **k: [])
    monkeypatch.setattr(rni, "collect_seeking_alpha_articles", lambda *a, **k: [])

    class _StubGraph:
        def ingest_source(self, **kw):
            return 1

    def _batch(title, url, key):
        return [{
            "title": title, "url": url, "publisher": "Reuters",
            "provider": "reuters", "text_or_summary": "x" * 60,
            "dedupe_key": key, "published_at": "",
        }]

    # Batch 1: tripwire headline -> pending confirmation, not active.
    monkeypatch.setattr(
        rni, "collect_rss_articles",
        lambda: _batch("Exchange halts trading as circuit breaker hits",
                       "https://reuters.com/a1", "k1"),
    )
    res1 = rni.run(graph=_StubGraph(), extract_claims=False)
    ks1 = res1["kill_switch"]
    assert ks1["active"] is False
    assert ks1["latch"]["latched"] is False
    assert ks1["count"] == 1  # headline count still reported

    # Batch 2: second independent tripwire batch -> latch engages.
    monkeypatch.setattr(
        rni, "collect_rss_articles",
        lambda: _batch("Second exchange halt confirmed",
                       "https://reuters.com/a2", "k2"),
    )
    res2 = rni.run(graph=_StubGraph(), extract_claims=False)
    ks2 = res2["kill_switch"]
    assert ks2["active"] is True
    assert ks2["latch"]["latched"] is True

    # Batch 3: quiet feed -> still latched (persistence across cycles).
    monkeypatch.setattr(rni, "collect_rss_articles", lambda: [])
    res3 = rni.run(graph=_StubGraph(), extract_claims=False)
    ks3 = res3["kill_switch"]
    assert ks3["active"] is True
    assert ks3["count"] == 0
    assert ks3["latch"]["latched"] is True
    assert ks3["limitation"].startswith("Kill-switch state persists")


def test_macro_analyst_applies_market_data_to_latch(tmp_path, monkeypatch):
    import intelligence.graph_nodes as gn
    from intelligence.graph_nodes import MacroAnalystNode
    from kill_switch_latch import KillSwitchLatch

    monkeypatch.setenv("D9_LATCH_PATH", str(tmp_path / "latch.json"))
    latch = KillSwitchLatch(path=str(tmp_path / "latch.json"))
    monkeypatch.setattr(gn, "get_kill_switch_latch", lambda: latch)
    latch.apply_market_data(halt_flag=True)

    node = MacroAnalystNode.__new__(MacroAnalystNode)
    node.laya = None
    node.validator = None

    monkeypatch.setattr(gn, "DecisionFeatureBlock", gn.DecisionFeatureBlock)

    fb = node._build_feature_block(
        "EURUSD", {}, {"vix": 42.0},
        kill_switch={"active": True, "count": 1},
    )
    assert fb.is_exchange_halt is True

    # Calm market data clears the latch -> next cycle active flag falls.
    latch.apply_market_data(vix=15.0)
    assert latch.snapshot()["latched"] is False
    fb2 = node._build_feature_block(
        "EURUSD", {}, {"vix": 15.0},
        kill_switch=None,  # no news this cycle
    )
    assert fb2.is_exchange_halt is False
