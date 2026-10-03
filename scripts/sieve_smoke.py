#!/usr/bin/env python3
"""Live Sieve smoke test: one small public page, end to end.

Spends a few credits.  Starts a run against https://quotes.toscrape.com, polls
to completion, then downloads every delivered file and reports schema
conformance.  Never prints the API key.

Usage:
    python scripts/sieve_smoke.py [--max-wait 900] [--out artifacts/sieve]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import sieve_client  # noqa: E402

INSTRUCTION = "Extract the text and author of each quote"
TARGET_URLS = ["https://quotes.toscrape.com"]
FIELDS = ["text", "author"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sieve live smoke test")
    parser.add_argument("--max-wait", type=float, default=900.0,
                        help="give up polling after this many seconds")
    parser.add_argument("--out", default="artifacts/sieve")
    parser.add_argument("--target", default=TARGET_URLS[0])
    args = parser.parse_args()

    if not sieve_client.is_configured():
        print("SIEVE_API_KEY is not configured; run scripts/sieve_login.py first.")
        return 2

    runner = sieve_client.SieveRunner()
    print(f"Starting run against {args.target} ...")
    started = runner.start(INSTRUCTION, target_urls=[args.target], fields=FIELDS,
                           table_shape="long", compliance_mode="regular")
    session_id = started["session_id"]
    print(f"202 accepted  session_id={session_id}  poll={started['poll']}")

    deadline = time.monotonic() + float(args.max_wait)
    delay = sieve_client.POLL_INITIAL_S
    info = None
    while True:
        info = runner.poll_once(session_id)
        state = info.get("state")
        print(f"  status={state} turns={info.get('turns')}")
        if info.get("terminal"):
            break
        if time.monotonic() >= deadline:
            print(f"Timed out after {args.max_wait}s; run {session_id} is still active.")
            return 1
        time.sleep(delay)
        delay = sieve_client.next_poll_delay(delay)

    if info.get("state") == "refused":
        print(f"Run refused (terminal): code={info.get('refusal_code')}")
        return 1

    print(f"Done. summary: {info.get('summary')!r}")
    print(f"schema_conformance={info.get('schema_status')} "
          f"present_as_clean={info.get('present_as_clean')}")
    result = info.get("result")
    if isinstance(result, list):
        print(f"inline result rows: {len(result)}")
    elif result is not None:
        print(f"inline result: {type(result).__name__}")

    files = info.get("files") or []
    print(f"delivered files: {len(files)}")
    for file_info in files:
        dest = runner.client.download_file(file_info, args.out)
        print(f"  wrote {dest} ({file_info.get('size')} bytes, {file_info.get('ext')})")

    print(f"session_id={session_id} (recorded durably in sieve_runs.db)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
