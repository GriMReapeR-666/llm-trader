# Task 2 Brief: Dynamic Order & Portfolio Sizing Manager

**Files:**
- Modify: `core/quant_gates_and_pipeline.py`
- Modify/Test: `tests/test_quant_gates_and_pipeline.py`

**Requirements:**
1. Implement `OrderType(str, Enum)`: `MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`, `TWAP`, `VWAP`, `ICEBERG`.
2. Implement `OrderRequest` dataclass:
   `symbol`, `side`, `total_quantity`, `order_type`, `price`, `stop_price`, `child_orders`, `created_at`, `execution_authority = False`, `to_dict()`.
3. Implement `ExecutionOrderManager`:
   - `create_order(symbol, side, quantity, order_type, price, stop_price, slices, iceberg_display_qty) -> OrderRequest`:
     Slices parent order into child slices for TWAP/VWAP/Iceberg.
   - `calculate_dynamic_leverage(base_leverage, current_drawdown, kill_dd) -> float`:
     Scales leverage down smoothly towards zero as current drawdown approaches `kill_dd`.
4. Add `test_execution_order_manager` in `tests/test_quant_gates_and_pipeline.py`.
5. Run tests with `rtk pytest tests/test_quant_gates_and_pipeline.py -k test_execution_order_manager -o addopts="" -v`.
6. Commit with `rtk git commit -m "feat(execution): implement ExecutionOrderManager with TWAP, VWAP, Iceberg slicing and dynamic leverage"`.

**Report:**
Write full task report to `.superpowers/sdd/2026-10-23-quant-gates-and-deployment-pipeline/task-2-report.md`.
