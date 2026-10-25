"""Ratchet: direct broker reads outside the locked facade (PH4-N1).

Every raw ``mt5.positions_get / orders_get / history_* / account_info /
symbol_info* / copy_rates_*`` call bypasses the gateway's MT5 lock and races
order_send's ``last_error`` attribution. Money paths (order construction,
position management, risk limits, reconciliation, guardian, margin) go through
``core.broker_reads``. What remains is boot, preflight, diagnostics, research
scripts and CLI display code, plus the broker_time calibration primitive.

The ratchet pins the residual count: it must never grow. Drive it to 0 by
migrating the remaining sites to broker_reads (or deleting dead code), then
lower MAX_RESIDUAL_DIRECT_READS.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

READ_ATTRS = {
    "positions_get",
    "orders_get",
    "history_deals_get",
    "history_orders_get",
    "account_info",
    "symbol_info_tick",
    "symbol_info",
    "copy_rates_from_pos",
    "copy_rates_range",
}

# Files whose locked reads live in core/broker_reads.py and core/order_gateway.py
# (already under get_mt5_lock) are excluded from the count.
EXCLUDED = {"core/broker_reads.py", "core/order_gateway.py"}

# Residual as of PR 4.5 + locked-reads migration: boot, preflight,
# diagnostics, research scripts, CLI display, and the time calibration primitive.
MAX_RESIDUAL_DIRECT_READS = 23


def _count_residual_direct_reads():
    per_file = {}
    total = 0
    roots = [ROOT / "core", ROOT / "data", ROOT / "intelligence", ROOT / "operations"]
    files = []
    for d in roots:
        if d.is_dir():
            files.extend(d.rglob("*.py"))
    files.append(ROOT / "mt5_trader.py")
    for py_file in files:
        rel = py_file.relative_to(ROOT).as_posix()
        if rel in EXCLUDED:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue
        n = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in READ_ATTRS
        )
        if n:
            per_file[rel] = n
            total += n
    return total, per_file


def test_residual_direct_broker_reads_do_not_grow():
    total, per_file = _count_residual_direct_reads()
    assert total <= MAX_RESIDUAL_DIRECT_READS, (
        f"Direct broker reads grew to {total} (ratchet: {MAX_RESIDUAL_DIRECT_READS}): {per_file}. "
        "Route new reads through core.broker_reads."
    )


def test_locked_facade_covers_all_read_kinds():
    """The facade must expose every read kind the ratchet counts."""
    import core.broker_reads as br
    for name in [
        "broker_positions_get",
        "broker_orders_get",
        "broker_history_orders_get",
        "broker_history_deals_get",
        "broker_account_info",
        "broker_symbol_info",
        "broker_symbol_info_tick",
        "broker_copy_rates_from_pos",
        "broker_copy_rates_range",
    ]:
        assert callable(getattr(br, name, None)), f"facade missing {name}"
