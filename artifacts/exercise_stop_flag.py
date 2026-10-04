"""End-to-end exercise for MT5 Trader graceful stop flag (Task 4).

Streams output from a running trader process, injects the stop flag
once cycle execution is active, and verifies graceful CrashRecovery cleanup.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

# Ensure safe printing on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def safe_str(s: str) -> str:
    return s.encode("ascii", errors="replace").decode("ascii")

def main() -> int:
    stop_file = os.path.abspath("artifacts/trader.stop")
    consumed_file = stop_file + ".consumed"

    for f in [stop_file, consumed_file]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                pass

    env = os.environ.copy()
    env["MT5_TRADER_STOP_FILE"] = stop_file
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        sys.executable,
        "-u",
        "mt5_trader.py",
        "llm-trade",
        "--paper",
        "--symbol",
        "EURUSD",
        "--timeframe",
        "M15",
        "--loops",
        "10",
    ]

    print(f"[*] Starting trader with MT5_TRADER_STOP_FILE={stop_file}...")
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    flag_injected = False
    captured_lines = []
    start_time = time.time()
    max_wait = 240  # 4 minutes max

    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                captured_lines.append(line)
                stripped = line.rstrip()
                if any(k in stripped for k in ["CYCLE", "MARKET HOLIDAY", "pulse", "Stop flag", "Crash recovery", "DECISION"]):
                    print(f"  [trader] {safe_str(stripped)}")

                # Inject flag once cycle 1 is actively running or holiday routine active
                if not flag_injected and ("MARKET HOLIDAY DETECTED" in line or "CYCLE 1" in line or "pulse in" in line):
                    print(f"[*] Trigger detected ({safe_str(stripped[:60])}...). Injecting stop flag...")
                    with open(stop_file, "w", encoding="utf-8") as fh:
                        fh.write("Operator requested graceful wind-down test\n")
                    flag_injected = True
                    print(f"[*] Stop flag written to {stop_file}")

            if time.time() - start_time > max_wait:
                print("[!] Exercise timed out after 240s! Terminating trader...")
                proc.kill()
                return 1

    finally:
        if proc.poll() is None:
            proc.kill()

    returncode = proc.wait()
    print(f"[*] Trader process exited with returncode {returncode}")

    full_stdout = "".join(captured_lines)

    # Assertions
    assert returncode == 0, f"Expected returncode 0, got {returncode}"
    assert not os.path.exists(stop_file), "Stop flag was not retired (still exists)"
    assert os.path.exists(consumed_file), "Consumed stop flag (.consumed) does not exist"

    required_phrases = [
        "Stop flag requested",
        "winding down gracefully",
        "Crash recovery: graceful shutdown initiated",
        "Crash recovery: shutdown complete",
        "CrashRecovery shutdown snapshot saved (graceful exit)",
    ]

    missing = [p for p in required_phrases if p not in full_stdout]
    if missing:
        print(f"[!] Missing required shutdown phrases in output: {missing}")
        return 1

    print("[+] All assertions passed successfully:")
    print("    - Stop flag recognized while process active")
    print("    - Stop flag retired and renamed to .consumed")
    print("    - Clean exit code 0")
    print("    - Crash recovery graceful shutdown initiated & completed")
    print("    - CrashRecovery shutdown snapshot saved (graceful exit)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
