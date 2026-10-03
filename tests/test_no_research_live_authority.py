"""CRITICAL #5 - the research layer must not be able to grant live authority.

``research/auto_promotion.py`` advertised a route to live execution in four
separate places, and each one was independently sufficient:

1. the transition table listed ``DEMO_PROVEN -> DEPLOYED`` as an automated
   target, with the only defence being a later branch in ``promote_strategy``;
2. ``DECAYING -> DEPLOYED`` let a *decaying* strategy return straight to live,
   reversing its own demotion with no evidence re-run;
3. ``_get_next_status`` handed out ``DEPLOYED`` as the machine-chosen next step
   from ``READY_FOR_DEPLOYMENT``;
4. ``_update_strategy_status`` wrote whatever status it was handed, with no
   transition check and no actor check - so any caller holding an AutoPromoter
   could deploy a strategy that had never passed a gate.

The module docstring claimed "explicit operator approval ... for the final
DEPLOYED transition" while none of that was enforced.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("core", "data", "intelligence", "operations", "research"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research.auto_promotion as mod  # noqa: E402
from research.auto_promotion import LIVE_AUTHORITY_STATUSES, AutoPromoter  # noqa: E402


@pytest.fixture()
def promoter(tmp_path):
    return AutoPromoter(base_dir=str(tmp_path))


def _journal_db(tmp_path, name):
    db = str(tmp_path / name)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE strategy_lifecycle (setup_id TEXT, state TEXT, "
                "updated_at TEXT, updated_by TEXT, reason TEXT)")
    con.execute("CREATE TABLE strategy_lifecycle_history (id INTEGER PRIMARY KEY, "
                "setup_id TEXT, from_state TEXT, to_state TEXT, actor TEXT, "
                "reason TEXT, evidence TEXT, created_at TEXT)")
    con.execute("CREATE TABLE model_registry (model_key TEXT, version TEXT, "
                "kind TEXT, status TEXT, metrics TEXT, created_at TEXT, "
                "reviewed_at TEXT, reviewed_by TEXT, review_note TEXT)")
    con.commit()
    con.close()
    return db


# --- 1. no automated transition reaches a live-authority status ---------

def test_no_automated_transition_reaches_live_authority(promoter):
    """Walk the whole table: no live-authority status may be reachable."""
    live = LIVE_AUTHORITY_STATUSES
    seen = set()
    frontier = ["CANDIDATE", "VALIDATING", "PAPER_PROVEN", "DEMO_PROVEN",
                "DECAYING", "READY_FOR_DEPLOYMENT", "DEPLOYED", "FROZEN",
                "REJECTED"]
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nxt in promoter._get_allowed_transitions(cur):
            assert nxt not in live, (
                "automated transition %s -> %s grants live authority" % (cur, nxt))
            frontier.append(nxt)


def test_no_automated_next_status_is_live_authority(promoter):
    for cur in ("CANDIDATE", "VALIDATING", "PAPER_PROVEN", "DEMO_PROVEN",
                "READY_FOR_DEPLOYMENT"):
        assert promoter._get_next_status(cur) not in LIVE_AUTHORITY_STATUSES


def test_decaying_cannot_return_to_live(promoter):
    """A demoted strategy must not be able to skip back to DEPLOYED."""
    assert "DEPLOYED" not in promoter._get_allowed_transitions("DECAYING")


# --- 2. the status writer refuses live authority from automation --------

def test_writer_refuses_live_authority_from_automation(promoter):
    with pytest.raises(PermissionError) as exc:
        promoter._update_strategy_status("sneaky", "DEPLOYED", {})
    assert "live-authority" in str(exc.value)
    assert "sneaky" in str(exc.value)


def test_writer_refuses_even_with_all_gates_passing(promoter):
    """Winning every gate is not authorisation. The sink is unconditional."""
    perfect = {"sample_size": {"passed": True}, "dsr": {"passed": True},
               "walkforward": {"passed": True}, "live_performance": {"passed": True},
               "no_divergence": {"passed": True}}
    with pytest.raises(PermissionError):
        promoter._update_strategy_status("sneaky", "DEPLOYED", perfect)


def test_writer_still_permits_non_live_statuses(promoter, tmp_path):
    """The guard must not brick the ordinary promotion path."""
    promoter.db_path = _journal_db(tmp_path, "registry.db")
    promoter._update_strategy_status("honest", "PAPER_PROVEN",
                                     {"sample_size": {"passed": True}})

    con = sqlite3.connect(promoter.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM strategy_lifecycle_history "
                      "WHERE setup_id='honest'").fetchone()
    assert row is not None
    assert row["to_state"] == "PAPER_PROVEN"
    assert row["actor"] == "auto_promoter"
    con.close()


def test_operator_actor_is_recorded_honestly(promoter, tmp_path):
    """An operator transition is allowed and must be attributed to the operator."""
    promoter.db_path = _journal_db(tmp_path, "signed.db")
    promoter._update_strategy_status("signed_off", "DEPLOYED", {}, actor="operator")

    con = sqlite3.connect(promoter.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM strategy_lifecycle_history "
                      "WHERE setup_id='signed_off'").fetchone()
    assert row["to_state"] == "DEPLOYED"
    assert row["actor"] == "operator", "operator action misattributed to automation"
    con.close()


# --- 3. the docstring must not claim more than the code enforces ---------

def test_docstring_claims_match_enforcement():
    text = mod.__doc__ or ""
    assert "READY_FOR_DEPLOYMENT" in text
    # the old claim was that operator approval is "required" but unenforced
    assert "All other transitions can be automated" not in text
