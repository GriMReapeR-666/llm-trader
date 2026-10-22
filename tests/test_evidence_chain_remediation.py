"""Evidence-chain remediation: broker reconciliation -> R-multiples -> lifecycle -> rewards.

These cover the three audit items that share one root cause (a starved evidence
chain):

* **Item 6** -- reconciliation never persisted the broker-execution evidence that
  ``research_validation.broker_reconciled_tickets()`` reads, so every
  broker-reconciled gate downstream stayed shut even on a CLEAN report.
* **Item 7** -- lifecycle evaluation had no offline entry point and reported a
  silent zero sample instead of why it had none.
* **Item 8** -- the reward ledger's only emitter was a live close callback, so
  ``reward_events`` stayed pinned to the run that happened to close last.

The safety properties asserted here are the point: evidence is minted only from
an independent broker record that MATCHED a journal ticket, never from the
journal's own rows, and no path in this file can promote a strategy.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from core.trading_safety import SCALP_MAGIC, TradeProposal, TradingStore
from intelligence.feature_schema import SCHEMA_HASHES, values_hash
from operations.daily_reconciliation import (
    JOURNAL_DERIVED_SOURCE,
    PAPER_BROKER_SOURCE,
    RECONCILED_CLOSE_EVENT,
    adapt_paper_deals,
    compare_day,
)
from operations.reward_attribution import (
    attribute_reconciled_history,
    ledger_freshness,
)
from operations.learning_loop import run_daily_review
from operations.strategy_evaluation import run_offline
from research.research_validation import broker_reconciled_tickets, labelled_dataset


SETUP_ID = "remediation_setup_v1"
FEATURE_VERSION = next(iter(SCHEMA_HASHES))


def _window():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=1), now + timedelta(hours=1)


def _seed_closed_trade(store: TradingStore, ticket: int, pnl: float = 10.0,
                       risk: float = 10.0) -> None:
    """Write one closed, deterministically labelled journal trade."""
    values = {"close": 1.20478, "rsi_14": 41.5, "atr_14": 0.01363}
    payload = {"proposal_features": {
        "setup_id": SETUP_ID,
        "feature_schema_version": FEATURE_VERSION,
        "feature_schema_hash": SCHEMA_HASHES[FEATURE_VERSION],
        "feature_values": values,
        "feature_values_hash": values_hash(values),
    }}
    proposal = TradeProposal("scalp", "EURUSD", "BUY", 1.1000, 1.0950, 1.1100, risk, payload["proposal_features"])
    store.record_open_trade(ticket, proposal, 1.0, risk, payload, provenance="mt5_broker")
    store.record_close(ticket, 1.1050, pnl, "target")
    with store._connect() as con:
        con.execute(
            """INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status)
               VALUES(?,?,?,?,?,?, 'AUTO')""",
            (ticket, "WIN" if pnl >= 0 else "LOSS", "r_multiple",
             pnl / risk, datetime.now(timezone.utc).isoformat(), "deterministic_v1"))


def _paper_close(ticket: int, pnl: float = 10.0, magic: int = SCALP_MAGIC) -> dict:
    """A real PaperBroker close record (paper/history shape, not a journal row)."""
    return {"ticket": ticket, "symbol": "EURUSD", "side": "buy", "volume": 1.0,
            "price_open": 1.1000, "price_close": 1.1050, "profit": pnl,
            "magic": magic, "status": "CLOSED", "close_time": datetime.now(timezone.utc).timestamp()}


def _reports(store: TradingStore) -> list:
    with store._connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT event_type,intent_key,order_ticket,deal_ticket,position_ticket,payload "
            "FROM broker_execution_reports ORDER BY id")]


# ── Item 6: the broker-evidence sink ────────────────────────────────────────

def test_adapt_paper_deals_labels_independent_and_journal_records():
    paper = adapt_paper_deals([_paper_close(1)])
    assert paper[0]["reconciliation_source"] == PAPER_BROKER_SOURCE
    assert paper[0]["price"] == pytest.approx(1.1050)

    journal_row = {"ticket": 2, "magic": SCALP_MAGIC, "pnl": 10.0,
                   "closed_at": datetime.now(timezone.utc).isoformat(),
                   "exit_reason": "target", "setup_id": SETUP_ID, "payload": "{}"}
    replayed = adapt_paper_deals([journal_row])
    assert replayed[0]["reconciliation_source"] == JOURNAL_DERIVED_SOURCE


def test_clean_reconciliation_mints_broker_evidence_that_opens_the_gate(tmp_path):
    """The whole item: a CLEAN paper reconciliation must produce the row that
    broker_reconciled_tickets() reads, otherwise R-multiples never exist."""
    path = str(tmp_path / "evidenced.db")
    store = TradingStore(path)
    ticket = 4242
    _seed_closed_trade(store, ticket, pnl=10.0, risk=10.0)

    assert ticket not in broker_reconciled_tickets(path)
    assert labelled_dataset(path)["sample_size"] == 0

    start, end = _window()
    report = compare_day(store, adapt_paper_deals([_paper_close(ticket, 10.0)]), start, end)

    assert report["status"] == "CLEAN"
    assert report["reconciled_execution_evidence"]["written"] == 1
    rows = _reports(store)
    assert len(rows) == 1
    assert rows[0]["event_type"] == RECONCILED_CLOSE_EVENT
    assert rows[0]["position_ticket"] == ticket
    payload = json.loads(rows[0]["payload"])
    assert payload["broker_net_pnl"] == pytest.approx(10.0)
    assert payload["journal_net_pnl"] == pytest.approx(10.0)
    assert payload["source"] == PAPER_BROKER_SOURCE

    # The gate the audit says was permanently shut.
    assert ticket in broker_reconciled_tickets(path)
    dataset = labelled_dataset(path)
    assert dataset["sample_size"] == 1
    assert dataset["rows"][0]["r_multiple"] == pytest.approx(1.0)


def test_journal_replayed_deals_mint_no_evidence(tmp_path):
    """Comparing the journal against itself proves nothing and must not count."""
    path = str(tmp_path / "tautology.db")
    store = TradingStore(path)
    ticket = 77
    _seed_closed_trade(store, ticket)
    start, end = _window()

    # A faithful replay: the "broker" side is the journal row with its P&L
    # copied across, so the comparison really is CLEAN and really is vacuous.
    replay = [{"ticket": ticket, "magic": SCALP_MAGIC, "symbol": "EURUSD",
               "volume": 1.0, "profit": 10.0, "close_time": datetime.now(timezone.utc).timestamp(),
               "closed_at": datetime.now(timezone.utc).isoformat(),
               "exit_reason": "target", "setup_id": SETUP_ID, "payload": "{}"}]
    deals = adapt_paper_deals(replay)
    assert deals[0]["reconciliation_source"] == JOURNAL_DERIVED_SOURCE

    report = compare_day(store, deals, start, end)

    assert report["status"] == "CLEAN"           # still reports on its own terms
    assert report["reconciled_execution_evidence"]["written"] == 0
    assert report["reconciled_execution_evidence"]["refused_journal_derived"] == 1
    assert _reports(store) == []
    assert ticket not in broker_reconciled_tickets(path)
    assert labelled_dataset(path)["sample_size"] == 0


def test_discrepancies_never_mint_evidence(tmp_path):
    """Unmatched or mismatched activity stays an operator work item."""
    path = str(tmp_path / "mismatch.db")
    store = TradingStore(path)
    matched, absent = 501, 502
    _seed_closed_trade(store, matched, pnl=10.0)
    _seed_closed_trade(store, absent, pnl=10.0)
    start, end = _window()

    report = compare_day(store, [
        {"position_id": matched, "magic": SCALP_MAGIC, "entry": 1, "profit": 99.0},   # P&L mismatch
        {"position_id": 999, "magic": SCALP_MAGIC, "entry": 1, "profit": 5.0},         # unknown to journal
    ], start, end)

    assert report["status"] == "ATTENTION"
    assert report["reconciled_execution_evidence"]["written"] == 0
    assert report["reconciled_execution_evidence"]["refused_discrepant"] == 1
    assert _reports(store) == []


def test_repeated_reconciliation_does_not_duplicate_evidence(tmp_path):
    """broker_execution_reports is append-only; a daily re-run must not multiply it."""
    path = str(tmp_path / "idempotent.db")
    store = TradingStore(path)
    ticket = 909
    _seed_closed_trade(store, ticket)
    start, end = _window()

    first = compare_day(store, adapt_paper_deals([_paper_close(ticket)]), start, end)
    second = compare_day(store, adapt_paper_deals([_paper_close(ticket)]), start, end)

    assert first["reconciled_execution_evidence"]["written"] == 1
    assert second["reconciled_execution_evidence"]["written"] == 0
    assert second["reconciled_execution_evidence"]["already_recorded"] == 1
    assert len(_reports(store)) == 1


def test_dry_run_emits_no_evidence(tmp_path):
    path = str(tmp_path / "dryrun.db")
    store = TradingStore(path)
    ticket = 31
    _seed_closed_trade(store, ticket)
    start, end = _window()

    report = compare_day(store, adapt_paper_deals([_paper_close(ticket)]), start, end,
                         emit_execution_evidence=False)
    assert report["reconciled_execution_evidence"]["written"] == 0
    assert _reports(store) == []


def test_run_daily_review_reconciles_and_reports_honestly(tmp_path, monkeypatch):
    """The executor used to return status=COMPLETED while doing nothing at all."""
    from operations import learning_loop

    path = str(tmp_path / "daily.db")
    store = TradingStore(path)
    ticket = 808
    _seed_closed_trade(store, ticket)
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", path)

    result = learning_loop.run_daily_review({"paper_history": [_paper_close(ticket)], "run_id": "run-live"})
    assert result["status"] == "COMPLETED"
    assert result["broker_source"] == PAPER_BROKER_SOURCE
    assert result["daily_reconciliation"]["reconciled_execution_evidence"]["written"] == 1
    assert result["reward_attribution"]["emitted_count"] == 1
    assert result["execution_authority"] is False


def test_run_daily_review_without_broker_source_is_not_a_silent_success(tmp_path, monkeypatch):
    from operations import learning_loop

    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", str(tmp_path / "empty.db"))
    monkeypatch.setattr(learning_loop, "_resolve_broker_deals", lambda *a, **k: None)

    result = learning_loop.run_daily_review({})
    assert result["daily_reconciliation"]["status"] == "SKIPPED_NO_BROKER_SOURCE"
    assert result["execution_authority"] is False


def test_run_daily_review_reconciles_through_the_orchestrator_context(tmp_path, monkeypatch):
    """The scheduler dispatches with context=self (llm_orchestrator.py:6052), so this
    is the production path -- the one that must reach the paper broker on the context.

    It also pins the result shape: ``daily_reconciliation`` is the report, not the
    wrapper that carries it.
    """
    from operations import learning_loop

    path = str(tmp_path / "ctx.db")
    store = TradingStore(path)
    ticket = 909
    _seed_closed_trade(store, ticket)
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", path)

    class _Ctx:
        """Stands in for the orchestrator: has a paper broker, and its own
        ``_execute_daily_review`` which skips reconciliation in paper mode."""

        paper_broker = type("_PB", (), {"history": [_paper_close(ticket)]})()

        def _execute_daily_review(self, payload):
            return {"summary": {}, "daily_reconciliation": None, "execution_authority": False}

    result = learning_loop.run_daily_review({}, context=_Ctx())

    assert result["summary"] == {}                       # delegated output preserved
    assert result["broker_source"] == PAPER_BROKER_SOURCE
    assert result["daily_reconciliation"]["status"] == "CLEAN"
    assert result["daily_reconciliation"]["reconciled_execution_evidence"]["written"] == 1
    assert result["reward_attribution"]["emitted_count"] == 1
    assert ticket in broker_reconciled_tickets(path)
    assert result["execution_authority"] is False


def test_run_daily_review_does_not_duplicate_a_live_reconciliation(tmp_path, monkeypatch):
    """In live mode the orchestrator already reconciled; do not mint twice."""
    from operations import learning_loop

    path = str(tmp_path / "nodup.db")
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", path)
    already = {"status": "CLEAN", "period_start": "x", "period_end": "y"}

    class _Ctx:
        paper_broker = type("_PB", (), {"history": [_paper_close(1)]})()

        def _execute_daily_review(self, payload):
            return {"summary": {}, "daily_reconciliation": already, "execution_authority": False}

    result = learning_loop.run_daily_review({}, context=_Ctx())
    assert result["daily_reconciliation"] is already
    assert "reward_attribution" not in result


# ── Item 8: rotate the reward ledger into the current cycle ──────────────────

def test_attribution_attaches_reconciled_closes_to_the_current_run(tmp_path):
    path = str(tmp_path / "rewards.db")
    store = TradingStore(path)
    ticket = 7001
    _seed_closed_trade(store, ticket, pnl=10.0, risk=10.0)
    start, end = _window()
    compare_day(store, adapt_paper_deals([_paper_close(ticket, 10.0)]), start, end)

    report = attribute_reconciled_history(path, run_id="run-current")

    assert report["status"] == "ATTRIBUTED"
    assert report["emitted_count"] == 1
    assert report["emitted"][0]["ticket"] == ticket
    assert report["emitted"][0]["r_net"] == pytest.approx(1.0)
    assert report["execution_authority"] is False

    with sqlite3.connect(path) as con:
        rows = con.execute("SELECT run_id, action, execution_authority FROM reward_events "
                           "WHERE action='terminal_trade_close'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "run-current"          # not a dead historical run id
    assert rows[0][2] == 0                     # never execution authority


def test_attribution_is_idempotent_across_scheduled_passes(tmp_path):
    path = str(tmp_path / "rewards_twice.db")
    store = TradingStore(path)
    ticket = 7002
    _seed_closed_trade(store, ticket)
    start, end = _window()
    compare_day(store, adapt_paper_deals([_paper_close(ticket)]), start, end)

    first = attribute_reconciled_history(path, run_id="run-a")
    second = attribute_reconciled_history(path, run_id="run-b")

    assert first["emitted_count"] == 1
    assert second["emitted_count"] == 0
    assert second["status"] == "NO_NEW_RECONCILED_CLOSES"
    assert second["skipped_already_attributed"] == 1


def test_attribution_refuses_unreconciled_closes(tmp_path):
    """No reconciliation, no reward: the ledger must not invent its own evidence."""
    path = str(tmp_path / "unreconciled.db")
    store = TradingStore(path)
    _seed_closed_trade(store, 7003)

    report = attribute_reconciled_history(path, run_id="run-current")

    assert report["status"] == "NO_NEW_RECONCILED_CLOSES"
    assert report["emitted_count"] == 0
    assert report["dataset_sample_size"] == 0
    assert ledger_freshness(path)["event_count"] == 0


def test_ledger_freshness_surfaces_stale_run_refs(tmp_path):
    from operations.reward_ledger import ensure_reward_schema

    path = str(tmp_path / "stale.db")
    ensure_reward_schema(path)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO reward_events(created_at,layer,agent,action,value,run_id,payload,"
                    "execution_authority) VALUES(?,?,?,?,?,?,?,0)",
                    (old, "L2", "MarketModel", "terminal_trade_close", 1.0, "run-from-september", "{}"))

    freshness = ledger_freshness(path)
    assert freshness["fresh"] is False
    assert freshness["freshness_hours"] > 24 * 30
    assert [r["run_id"] for r in freshness["oldest_run_ids"]] == ["run-from-september"]

    # One new event in the current cycle flips the bottom-line metric; the dead
    # run ref stays listed because it is still the only ref for those old rows.
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO reward_events(created_at,layer,agent,action,value,run_id,payload,"
                    "execution_authority) VALUES(?,?,?,?,?,?,?,0)",
                    (datetime.now(timezone.utc).isoformat(), "L2", "MarketModel",
                     "terminal_trade_close", 1.0, "run-current", "{}"))
    rotated = ledger_freshness(path)
    assert rotated["fresh"] is True
    assert rotated["freshness_hours"] < 1.0
    assert rotated["event_count"] == 2
    assert [r["run_id"] for r in rotated["oldest_run_ids"]] == ["run-from-september"]


# ── Item 7: offline lifecycle evaluation over paper-reconciled history ──────

def test_offline_lifecycle_scores_a_candidate_from_reconciled_paper_history(tmp_path):
    """The audit's ask: a CANDIDATE strategy can actually be scored offline."""
    import operations.strategy_lifecycle as slc

    path = str(tmp_path / "lifecycle.db")
    store = TradingStore(path)
    slc.register_candidate(path, SETUP_ID, reason="alpha mining")
    assert slc.current_states(path)[SETUP_ID] == "CANDIDATE"

    for index in range(3):
        ticket = 3000 + index
        _seed_closed_trade(store, ticket, pnl=10.0, risk=10.0)
    start, end = _window()
    compare_day(store, adapt_paper_deals([_paper_close(3000 + i, 10.0) for i in range(3)]), start, end)

    report = run_offline(path)
    assert report["evidence"]["status"] == "READY"
    assert report["evidence"]["admitted_rows"] == 3
    scored = [r for r in report["setups_evaluated"] if r["setup_id"] == SETUP_ID]
    assert scored and scored[0]["closed_samples"] == 3
    assert scored[0]["expectancy_r"] == pytest.approx(1.0)
    # Scoring is not promoting, and the sample floor still holds.
    assert scored[0]["decision"] == "CONTINUE_MONITORING"
    assert slc.current_states(path)[SETUP_ID] == "CANDIDATE"
    assert report["execution_authority"] is False


def test_offline_lifecycle_reports_why_it_scored_nothing(tmp_path):
    path = str(tmp_path / "starved.db")
    TradingStore(path)
    _seed_closed_trade(TradingStore(path), 4001)   # closed, labelled, but never reconciled

    report = run_offline(path)
    assert report["evidence"]["status"] == "NO_ADMISSIBLE_EVIDENCE"
    assert report["dataset_sample_size"] == 0
    assert report["evidence"]["excluded"]["unbrokered_trade"] == 1
    assert report["execution_authority"] is False


def test_offline_lifecycle_never_promotes_below_the_sample_floor(tmp_path):
    """A floor breach is a refusal, not a licence."""
    import operations.strategy_lifecycle as slc

    path = str(tmp_path / "floor.db")
    store = TradingStore(path)
    slc.register_candidate(path, SETUP_ID)
    _seed_closed_trade(store, 5001, pnl=10.0)
    start, end = _window()
    compare_day(store, adapt_paper_deals([_paper_close(5001, 10.0)]), start, end)

    report = run_offline(path)
    record = [r for r in report["setups_evaluated"] if r["setup_id"] == SETUP_ID][0]
    assert record["closed_samples"] == 1
    assert record["decision"] == "CONTINUE_MONITORING"
    assert "insufficient closed samples" in record["reason"]
    assert slc.current_states(path)[SETUP_ID] == "CANDIDATE"


def test_offline_lifecycle_cli_reports_json(tmp_path, capsys):
    from operations import strategy_evaluation

    path = str(tmp_path / "cli.db")
    TradingStore(path)
    _seed_closed_trade(TradingStore(path), 6001)

    assert strategy_evaluation.main(["--db", path]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["execution_authority"] is False
    assert payload["evidence"]["status"] == "NO_ADMISSIBLE_EVIDENCE"

    # Below the policy floor the CLI refuses rather than scoring on a smaller sample.
    with pytest.raises(SystemExit) as excinfo:
        strategy_evaluation.main(["--db", path, "--min-samples", "5"])
    assert excinfo.value.code == 2


# ── The acceptance criterion: one pass, the whole chain ─────────────────────

def test_paper_close_flows_through_to_scoring_and_rewards(tmp_path, monkeypatch):
    """Real PaperBroker closes -> reconciliation -> R-multiples -> lifecycle -> rewards.

    Uses the genuine ``core.order_execution.PaperBroker`` and each journal P&L is
    taken from the broker's own close result, not invented, so the reconciliation
    is a real agreement rather than a rigged one.
    """
    from core.order_execution import PaperBroker
    from operations.reward_attribution import ledger_freshness
    from operations.strategy_evaluation import run_offline

    path = str(tmp_path / "chain.db")
    store = TradingStore(path)
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", path)
    paper = PaperBroker()

    for i in range(5):
        entry = 1.1000 + i * 0.0001
        ticket = paper.market_order("EURUSD", "buy", 1.0, entry, 1.09, 1.12, "chain", magic=SCALP_MAGIC)["order"]
        values = {"close": entry, "rsi_14": 40.0 + i, "atr_14": 0.013}
        payload = {"proposal_features": {
            "setup_id": SETUP_ID,
            "feature_schema_version": FEATURE_VERSION,
            "feature_schema_hash": SCHEMA_HASHES[FEATURE_VERSION],
            "feature_values": values,
            "feature_values_hash": values_hash(values),
            "causal_edge_ids": [i + 1],
            "opportunity_candidate_id": f"cand-{i}",
        }}
        store.record_open_trade(
            ticket, TradeProposal("scalp", "EURUSD", "BUY", entry, 1.09, 1.12, 1.0,
                                  payload["proposal_features"]),
            1.0, 1.0, payload, provenance="mt5_broker")
        close_px = entry + (0.0005 if i % 2 == 0 else -0.0002)
        pnl = float(paper.close_position(ticket, close_px, magic=SCALP_MAGIC)["profit"])
        store.record_close(ticket, close_px, pnl, "target")
        with sqlite3.connect(path) as con:
            con.execute(
                """INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status)
                   VALUES(?,?,?,?,?,?,'AUTO')""",
                (ticket, "WIN" if pnl > 0 else "LOSS", "r_multiple", pnl / 1.0,
                 datetime.now(timezone.utc).isoformat(), "deterministic_v1"))

    assert len(paper.history) == 5
    assert broker_reconciled_tickets(path) == set()      # starved before
    assert labelled_dataset(path)["sample_size"] == 0

    out = run_daily_review({"paper_history": paper.history, "run_id": "run-chain"})
    recon = out["daily_reconciliation"]
    assert recon["status"] == "CLEAN"
    assert recon["reconciled_execution_evidence"]["written"] == 5

    dataset = labelled_dataset(path)                     # the gate that was shut
    assert dataset["sample_size"] == 5
    assert all(r["r_multiple"] == pytest.approx(r["pnl"] / r["risk_amount"]) for r in dataset["rows"])

    scored = run_offline(path)
    assert scored["evidence"]["status"] == "READY"
    assert scored["setups_evaluated"][0]["setup_id"] == SETUP_ID
    assert scored["setups_evaluated"][0]["closed_samples"] == 5

    attribution = out["reward_attribution"]
    assert attribution["emitted_count"] == 5
    assert attribution["freshness"]["fresh"] is True
    assert ledger_freshness(path)["freshness_hours"] < 1.0
    with sqlite3.connect(path) as con:
        runs = {r[0] for r in con.execute("SELECT run_id FROM reward_events")}
        authority = con.execute("SELECT MAX(execution_authority) FROM reward_events").fetchone()[0]
    assert runs == {"run-chain"}                          # no stale run refs
    assert authority == 0                                 # never execution authority


def test_cpcv_refuses_unreconciled_and_fixture_rows(tmp_path, monkeypatch):
    """``run_weekly_research`` used to read ``trade_labels`` directly, so the 150
    quarantined synthetic fixtures in the live journal satisfied the >= 40 sample
    floor on their own. The floor is unchanged; the admission is now honest.
    """
    from operations import learning_loop

    path = str(tmp_path / "cpcv.db")
    store = TradingStore(path)
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", path)

    # 60 closed, deterministically labelled trades -- but no broker evidence,
    # and flagged as fixtures exactly like the ones in the production journal.
    for i in range(60):
        ticket = 8000 + i
        with store._connect() as con:
            con.execute(
                """INSERT INTO trades(ticket,magic,strategy,symbol,side,volume,entry,stop_loss,
                       take_profit,risk_amount,opened_at,closed_at,exit_price,pnl,exit_reason,payload,provenance)
                   VALUES(?,?,'scalp','EURUSD','BUY',1.0,1.1,1.0,1.3,10.0,?,?,1.3,15.0,'target',?,'synthetic')""",
                (ticket, SCALP_MAGIC, datetime.now(timezone.utc).isoformat(),
                 datetime.now(timezone.utc).isoformat(),
                 json.dumps({"synthetic": True, "source": "synthetic_fixture_legacy"})))
            con.execute(
                """INSERT INTO trade_labels(ticket,label,rationale,r_multiple,created_at,label_rule,review_status)
                   VALUES(?,?,?,?,?,?,'AUTO')""",
                (ticket, "WIN", "r_multiple", 1.5, datetime.now(timezone.utc).isoformat(),
                 "deterministic_v1"))

    result = learning_loop.run_weekly_research({})
    assert result["cpcv_report"]["status"] == "INSUFFICIENT_SAMPLES_FOR_CPCV"
    assert result["cpcv_report"]["sample_size"] == 0
    assert result["cpcv_report"]["minimum_sample"] == 40      # threshold untouched
    assert result["execution_authority"] is False


def test_run_daily_review_attributes_when_delegated_reconciliation_writes_evidence(tmp_path, monkeypatch):
    """When the orchestrator delegates daily reconciliation and writes evidence,
    reward attribution must be triggered rather than silently skipped."""
    from operations import learning_loop

    path = str(tmp_path / "delegated_attr.db")
    store = TradingStore(path)
    ticket = 8801
    _seed_closed_trade(store, ticket, pnl=15.0, risk=15.0)
    monkeypatch.setenv("LLM_TRADER_JOURNAL_PATH", path)

    start, end = _window()
    compare_rep = compare_day(store, adapt_paper_deals([_paper_close(ticket, 15.0)]), start, end)
    assert compare_rep.get("reconciled_execution_evidence", {}).get("written") == 1

    class _Ctx:
        paper_broker = type("_PB", (), {"history": [_paper_close(ticket, 15.0)]})()

        def _execute_daily_review(self, payload, paper_mode=True):
            return {"summary": {}, "daily_reconciliation": compare_rep, "execution_authority": False}

    result = learning_loop.run_daily_review({"run_id": "delegated-run"}, context=_Ctx())
    assert result["daily_reconciliation"] == compare_rep
    assert result.get("reward_attribution", {}).get("status") == "ATTRIBUTED"
    assert result["reward_attribution"]["emitted_count"] == 1
    assert result["reward_attribution"]["emitted"][0]["ticket"] == ticket
