"""Guard: every raw ``FROM trades`` read is inventoried and classified.

The 150-row synthetic fixture graded as real performance because ~33 modules
read the trades table directly, bypassing research_validation's broker
reconciliation gate.  This test makes that bypass surface in CI: any new or
count-changed reader forces a conscious inventory edit stating whether it is
operational state, display, a test, the gate itself, or known evidence debt.
The guard file itself quotes the pattern in prose and is excluded from the
scan; it never executes SQL against a journal.

ponytail: file-granularity ceiling -- site counts are pinned per file, but the
lint cannot judge an individual site's intent; moving a site out of debt still
requires the routing code plus an inventory update.  Upgrade path: SQL-level
taint analysis if a debt file ever needs internal mixing of debt/operational.
"""
from __future__ import annotations

import io
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# scratch/: throwaway diagnostics never imported by the app or fed to
# promotion.  graphify-out/ is generated; node_modules/ is vendored.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", "graphify-out", "scratch"}

_FROM_TRADES = re.compile(r"FROM\s+trades\b", re.I)

_SELF = os.path.relpath(__file__, _ROOT).replace(os.sep, "/")

# file -> (category, site_count).  Categories:
#   gate        reads via research_validation broker reconciliation only
#   operational open-risk, reconciliation, metrics sync, memory, migration,
#               health/audit plumbing -- state, not performance claims
#   display     UI/API panels; never gates promotion or live behavior
#   test        fixtures against their own tmp journals
#   script      root-level one-off tools
#   debt        evidence readers NOT yet broker-routed (may only shrink)
INVENTORY: dict = {
    "core/graph_state_persistence.py": ("operational", 2),
    "core/quantitative_metrics_service.py": ("gate", 2),
    "core/self_improvement_loop.py": ("gate", 2),
    "core/trading_safety.py": ("operational", 10),
    "core/trading_tools.py": ("gate", 1),
    "intelligence/trading_memory.py": ("operational", 6),
    "mt5_trader.py": ("gate", 2),
    "operations/analytics.py": ("gate", 1),
    "operations/health_alerts.py": ("operational", 1),
    "operations/improvement_pipeline.py": ("gate", 1),
    "operations/journal_audit.py": ("gate", 11),
    "operations/journal_migration.py": ("operational", 2),
    "operations/legacy_reconciliation.py": ("operational", 3),
    "operations/meta_improvement_orchestrator.py": ("gate", 1),
    "operations/prometheus_exporter.py": ("operational", 3),
    "research/advanced_rl_pipeline.py": ("gate", 1),
    "research/auto_promotion.py": ("gate", 1),
    "scripts/insert_sim.py": ("script", 1),
    "tools/inspection/payload_check.py": ("script", 1),
    "tools/inspection/pf_check.py": ("script", 1),
    "research/code_interpreter.py": ("gate", 1),
    "research/continuous_backtest_pipeline.py": ("gate", 4),
    "research/demo_sample_generator.py": ("operational", 1),
    "research/divergence_monitor.py": ("gate", 1),
    "research/research_validation.py": ("gate", 1),
    "research/training_orchestrator.py": ("gate", 1),
    "tests/test_legacy_reconciliation.py": ("test", 2),
    "tests/test_microstructure_scalp_pipeline_e2e.py": ("test", 1),
    "tests/test_mt5_backtest_pipeline_integration.py": ("test", 1),
    "tests/test_promotion_gate_journal.py": ("test", 1),
    "tests/test_strategy_lifecycle_engine.py": ("test", 1),
    "tools/inspection/sample_trades.py": ("script", 1),
    "tools/inspection/trade_stats.py": ("script", 3),
    "ui/gui_api.py": ("display", 5),
    "ui/live_journal_reader.py": ("display", 5),
}

# Evidence readers allowed to touch raw rows until routed through
# broker_reconciled_tickets()/labelled_dataset.  Growing this set fails.
DEBT = frozenset()

# Files claiming the gate role must keep the reconciliation API in source.
GATE_FILES = frozenset({
    "core/quantitative_metrics_service.py",
    "core/self_improvement_loop.py",
    "core/trading_tools.py",
    "mt5_trader.py",
    "operations/analytics.py",
    "operations/improvement_pipeline.py",
    "operations/journal_audit.py",
    "operations/meta_improvement_orchestrator.py",
    "research/advanced_rl_pipeline.py",
    "research/auto_promotion.py",
    "research/code_interpreter.py",
    "research/continuous_backtest_pipeline.py",
    "research/divergence_monitor.py",
    "research/research_validation.py",
    "research/training_orchestrator.py",
})

_ROUTING_MARKERS = (
    "broker_reconciled_tickets",
    "_admissible_trades",
    "_broker_confirmed",
    "labelled_dataset",
)


def _site_counts() -> dict:
    counts: dict = {}
    for root, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, _ROOT).replace(os.sep, "/")
            if rel == _SELF:
                continue
            try:
                text = io.open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            total = 0
            for line in text.split("\n"):
                if line.lstrip().startswith("#"):
                    continue
                total += len(_FROM_TRADES.findall(line))
            if total:
                counts[rel] = total
    return counts


def test_every_from_trades_reader_is_inventoried():
    unknown = sorted(set(_site_counts()) - set(INVENTORY))
    assert not unknown, (
        "unclassified FROM trades readers -- add to INVENTORY with a category "
        "(gate/operational/display/test/script/debt) in "
        "tests/test_from_trades_lint.py: %s" % unknown
    )


def test_inventory_matches_actual_site_counts():
    counts = _site_counts()
    mismatched = {
        rel: {"in_inventory": INVENTORY[rel][1], "actual": counts.get(rel)}
        for rel in INVENTORY
        if INVENTORY[rel][1] != counts.get(rel)
    }
    assert not mismatched, (
        "FROM trades sites were added or removed -- update INVENTORY "
        "(and DEBT if an evidence reader appeared): %s" % mismatched
    )


def test_evidence_debt_is_frozen():
    actual = {rel for rel, (cat, _) in INVENTORY.items() if cat == "debt"}
    assert actual == set(DEBT), (
        "evidence debt changed -- routed (remove from DEBT, recategorize): %s; "
        "new debt (routing work required, DEBT may only shrink): %s"
        % (sorted(set(DEBT) - actual), sorted(actual - set(DEBT)))
    )


def test_gate_files_keep_routing_markers():
    for rel in sorted(GATE_FILES):
        text = io.open(
            os.path.join(_ROOT, rel), encoding="utf-8", errors="replace"
        ).read()
        assert any(m in text for m in _ROUTING_MARKERS), (
            "%s claims the gate role but no longer references the broker "
            "reconciliation API %s" % (rel, _ROUTING_MARKERS)
        )
