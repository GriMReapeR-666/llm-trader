import sys, tempfile, os
sys.path.insert(0, "/private/tmp/props_04")
import test_props as T
from trading_safety import DeterministicValidator, TradingStore

for k, patch in sorted(T.PERTURBATIONS.items()):
    store = TradingStore(os.path.join(tempfile.mkdtemp(), "s.db"))
    v = DeterministicValidator(store, min_walk_forward_efficiency=0.5)
    m = T._market(); m.update(patch)
    r = v.validate(T._proposal(), m)
    print(f"{k:32s} approved={r.approved} reasons={r.reasons[:2]}")

# validate_order_params extra probes
from order_execution import validate_order_params
for args in [("buy", 1.0, None, None), ("buy", 1.0, float("nan"), 1.5), ("long", 1.0, 1.01, 0.5),
             ("buy", float("nan"), 0.99, 1.1), ("buy", 1.0, 0.99, None), ("buy", 1.0, 0.99, 1.005)]:
    try:
        validate_order_params(*args); print("ACCEPTED", args)
    except Exception as e:
        print("rejected", args, type(e).__name__)

# sizing worst case: broker min 0.001 step 0.001 and target volume 0.005 lots
import risk_manager
from types import SimpleNamespace
spec = SimpleNamespace(trade_tick_size=0.01, trade_tick_value=1.0, volume_min=0.001, volume_max=100, volume_step=0.001)
risk_manager.mt5 = T.FakeMT5(1000.0, spec)
rm = risk_manager.RiskManager(balance=1000.0, risk_per_trade=0.01, state_file=os.path.join(tempfile.mkdtemp(), "r.json"))
rm.spmc_filter = None
# 1R = $10; loss/lot = dist/0.01*1 ; choose dist so ideal volume = 0.005 -> loss/lot=2000 -> dist=20
v = rm.calc_position_size("XYZ", 1000.0, 980.0, confidence=1.0)
print("sizing: ideal 0.005 lots ->", v, "risk $", v * 2000, "vs 1R $10")

from margin_safety import MarginSafetyGate
try:
    MarginSafetyGate().assess_from_mt5("EURUSD", volume=0.1, price=1.1, side="buy")
except TypeError as e:
    print("order_execution.py:773 call signature ->", type(e).__name__, str(e)[:120])
