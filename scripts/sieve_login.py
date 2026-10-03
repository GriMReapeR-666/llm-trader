#!/usr/bin/env python3
"""Sieve device-login: mint an API key once, with the user present.

Flow (OAuth device grant):
  1. POST /api/auth/device/code -> user_code + verification_uri_complete
  2. print both; the USER opens the link, signs in, checks the code matches,
     and clicks Approve.  This tool never opens the link, signs in, or approves.
  3. poll POST /api/auth/device/token until approved / denied / expired.
  4. write the key straight into the project's secure store as ``sieve:api_key``
     (read by ``core.sieve_client.get_api_key``); never print, log, or commit it.

Phishing note: an attacker can start a device-code flow themselves and trick a
user into approving the attacker's code.  Only ever approve a code YOU started,
from a tool prompt you trust.

Usage:
    python scripts/sieve_login.py [--client-name "llm-trader"] [--base-url URL]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import sieve_client  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Sieve device login")
    parser.add_argument("--client-name", default="llm-trader",
                        help="self-reported tool name shown on the approval page")
    parser.add_argument("--base-url", default=None,
                        help="override the Sieve base URL")
    parser.add_argument("--max-rounds", type=int, default=1,
                        help="how many expired-links to tolerate before giving up")
    args = parser.parse_args()

    if sieve_client.is_configured():
        print("A Sieve key is already configured (env SIEVE_API_KEY or secure store).")
        print("Use Settings -> API keys on scrape.usesieve.com to rotate it instead.")
        return 0

    client = sieve_client.SieveClient(base_url=args.base_url)
    rounds = 0
    while True:
        rounds += 1
        try:
            code = client.request_device_code(args.client_name)
        except sieve_client.SieveError as exc:
            print(f"Could not start device login: {exc}")
            return 1

        print()
        print("=" * 68)
        print("  Approve this login to let the trading system use Sieve.")
        print("=" * 68)
        print(f"  1. Open:  {code.verification_uri_complete}")
        print(f"  2. Confirm the code shown on the page is:  {code.user_code}")
        print(f"  3. Sign in / sign up and click Approve.")
        print()
        print("  Only approve a code you started yourself.  If you did not")
        print(f"  start this, stop and close the page.  It expires in "
              f"{code.expires_in // 60} minutes.")
        print("=" * 68)
        print()

        interval = max(1, int(code.interval))
        deadline = time.monotonic() + max(30, int(code.expires_in))
        while True:
            if time.monotonic() >= deadline:
                print("The code expired before approval.")
                break
            time.sleep(interval)
            try:
                result = client.poll_device_token(code.device_code)
            except sieve_client.SieveError as exc:
                print(f"Polling failed: {exc}")
                return 1
            state = result.get("state")
            if state == "pending":
                continue
            if state == "slow_down":
                interval += 5
                continue
            if state == "denied":
                print("Login denied in the browser. Nothing was stored.")
                return 1
            if state == "expired":
                print("The code expired. Starting over.")
                break
            if state == "approved":
                # Write the key straight into the secure store; never echo it.
                sieve_client.store_api_key(result["api_key"])
                print("Approved. Key stored in the encrypted secure store "
                      "(reference 'sieve:api_key').")
                print(f"Key name: {result.get('key_name') or '(unnamed)'}")
                print("It was not printed, logged, or committed.")
                return 0
            print(f"Unexpected device-login state: {state!r}")
            return 1

        if rounds >= max(1, int(args.max_rounds)):
            print("Giving up after an expired code. Re-run to try again.")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
