"""Run test_macro_correct_benchmark with full crash capture (temp diagnostics helper)."""
import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import research.test_macro_correct_benchmark as bench
    bench.run_macro_correct_benchmark()
except BaseException:
    with open("artifacts/macro_correct_bench_crash.txt", "w", encoding="utf-8") as f:
        f.write(traceback.format_exc())
    raise
