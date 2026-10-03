"""
QKF Smoke Test
==============
One-command verification for quant_knowledge_framework:

    python smoke_test.py            # from quant_knowledge_framework/
    python smoke_test.py --verbose  # full probe output inline
    python smoke_test.py --fast     # probes only (~2s); launch preflight

Runs, in order:

1. the full pytest suite (same flags as CI convention here:
   -p no:warnings -n0 -q --tb=short),
2. ``artifacts/pu_qkf_e2e.py`` — the E2E validation battery (CLI subprocesses,
   agent tools over the real KB, data-integrity cross-checks, idempotence
   sandboxes; it mutates nothing on the real KB),
3. ``artifacts/pu_qkf_transcribe_probe.py`` — the transcribe-stage
   de-duplication probe (fresh sandbox, verifies reuse/idempotence semantics),
4. ``artifacts/pu_qkf_download_probe.py`` — the download-stage probe
   (stubbed yt-dlp: no network, verifies metadata/transcript layout and
   transcript_only mode flags in a sandbox).

Writes a self-contained report to ``../artifacts/qkf_smoke_report.txt`` with an
``END-MARKER`` footer (the session's channel-fabrication doctrine: trust only
marker-footed file output read back from disk), and exits 0 only if the test
suite passes AND both probes print a GREEN verdict.

E2E probe paths resolve relative to this script's location, so the command
works from any cwd; the probe itself must run with cwd = quant_knowledge_framework.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

QKF_ROOT = Path(__file__).resolve().parent
REPO_ROOT = QKF_ROOT.parent
E2E_PROBE = REPO_ROOT / "artifacts" / "pu_qkf_e2e.py"
TRANSCRIBE_PROBE = REPO_ROOT / "artifacts" / "pu_qkf_transcribe_probe.py"
DOWNLOAD_PROBE = REPO_ROOT / "artifacts" / "pu_qkf_download_probe.py"
REPORT_PATH = REPO_ROOT / "artifacts" / "qkf_smoke_report.txt"

PYTEST_CMD = [sys.executable, "-X", "utf8", "-m", "pytest", "tests",
              "-p", "no:warnings", "-n0", "-q", "--tb=short"]


def run_step(name: str, cmd, cwd: Path, timeout: int) -> tuple:
    """Run one step; return (ok, summary_line, combined_output)."""
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        elapsed = time.perf_counter() - t0
        ok = proc.returncode == 0
        return ok, f"{name}: {'OK' if ok else 'FAILED'} ({elapsed:.1f}s, exit={proc.returncode})", out
    except subprocess.TimeoutExpired:
        return False, f"{name}: TIMEOUT after {timeout}s", ""
    except FileNotFoundError as e:
        return False, f"{name}: MISSING ({e})", ""


def probe_verdict(output: str):
    """Extract (verdict, passed, failed) from a probe's printed summary."""
    m = re.search(r"VERDICT:\s*(GREEN|RED)", output)
    if not m:
        return None, None, None
    p = re.search(r"PASS:\s*(\d+)\s+WARN:\s*(\d+)\s+FAIL:\s*(\d+)", output)
    if p:
        return m.group(1), int(p.group(1)), int(p.group(3))
    # transcribe probe prints all_pass:true,"n":<checks> instead of PASS/WARN/FAIL
    a = re.search(r'"all_pass":\s*(true|false)', output)
    if a:
        n = re.search(r'"n":\s*(\d+)', output)
        total = int(n.group(1)) if n else 1
        all_ok = a.group(1) == "true"
        return m.group(1), (total if all_ok else 0), (0 if all_ok else total)
    return m.group(1), 0, 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QKF one-command smoke test")
    parser.add_argument("--verbose", action="store_true",
                        help="print full probe output to the console as well")
    parser.add_argument("--fast", action="store_true",
                        help="probes only: skip the pytest suite and E2E battery "
                             "(~2s total; used by launch-script preflight)")
    args = parser.parse_args()

    missing = [str(p) for p in (E2E_PROBE, TRANSCRIBE_PROBE, DOWNLOAD_PROBE) if not p.exists()]
    if missing:
        print("ERROR: probes not found next to repo root:\n  " + "\n  ".join(missing))
        return 2

    steps = []
    e2e_out = ""

    if args.fast:
        # Launch-preflight mode: sandbox probes only. The skipped steps are
        # recorded so the report stays an honest picture of what ran.
        steps.append((True, "pytest suite: SKIPPED (--fast)"))
        steps.append((True, "E2E validation battery: SKIPPED (--fast)"))
        print("[FAST] probes only: pytest + E2E battery skipped", flush=True)
    else:
        # --- Step 1: pytest suite -------------------------------------------------
        ok, summary, out = run_step("pytest suite", PYTEST_CMD, QKF_ROOT, timeout=600)
        steps.append((ok, summary))
        m = re.search(r"(\d+) passed", out)
        steps.append((ok, f"  pytest: {m.group(1)} passed" if m else "  pytest: no 'N passed' line found"))
        if not ok and not args.verbose:
            # surface failures without dumping the whole log
            for line in out.splitlines():
                if line.startswith(("FAILED", "ERROR")) or "short test summary" in line:
                    print("   " + line)

        # --- Step 2: E2E validation battery ---------------------------------------
        env_note = " (E2E probe must run with cwd=quant_knowledge_framework)"
        ok, summary, e2e_out = run_step("E2E validation battery", [sys.executable, "-X", "utf8", str(E2E_PROBE)],
                                        QKF_ROOT, timeout=600)
        steps.append((ok, summary + env_note if not ok else summary))
        verdict, passed, failed = probe_verdict(e2e_out)
        if verdict:
            steps.append((verdict == "GREEN" and ok,
                          f"  E2E: {verdict} — {passed} pass / {failed} fail"))
        else:
            steps.append((False, "  E2E: no verdict line found (probe crashed before summary)"))

    # --- Step 3: transcribe-stage probe ---------------------------------------
    ok, summary, t_out = run_step("transcribe-stage probe", [sys.executable, "-X", "utf8", str(TRANSCRIBE_PROBE)],
                                  QKF_ROOT, timeout=300)
    steps.append((ok, summary))
    tv, tpass, tfail = probe_verdict(t_out)
    if tv:
        steps.append((tv == "GREEN" and ok, f"  transcribe probe: {tv} — {tpass} pass / {tfail} fail"))
    else:
        steps.append((False, "  transcribe probe: no verdict line found (probe crashed before summary)"))

    # --- Step 4: download-stage probe (stubbed yt-dlp, no network) ------------
    ok, summary, d_out = run_step("download-stage probe", [sys.executable, "-X", "utf8", str(DOWNLOAD_PROBE)],
                                  QKF_ROOT, timeout=300)
    steps.append((ok, summary))
    dv, dpass, dfail = probe_verdict(d_out)
    if dv:
        steps.append((dv == "GREEN" and ok, f"  download probe: {dv} — {dpass} pass / {dfail} fail"))
    else:
        steps.append((False, "  download probe: no verdict line found (probe crashed before summary)"))

    # --- Report ----------------------------------------------------------------
    all_ok = all(ok for ok, _ in steps)
    lines = []
    lines.append("# QKF Smoke Test Report")
    lines.append("")
    lines.append(f"Command: python smoke_test.py{' --fast' if args.fast else ''}  (cwd={Path.cwd()})")
    if args.fast:
        lines.append("Mode: fast (sandbox probes only; pytest + E2E battery skipped)")
    lines.append(f"Verdict: {'GREEN' if all_ok else 'RED'}")
    lines.append("")
    for ok, summary in steps:
        lines.append(f"[{'PASS' if ok else 'FAIL'}] {summary}")
    lines.append("")
    if args.verbose or not all_ok:
        for title, out in (
            ("E2E probe output", e2e_out),
            ("Transcribe probe output", t_out),
            ("Download probe output", d_out),
        ):
            if out:
                lines.append(f"## {title} (tail)")
                lines.append("```")
                lines.extend(out.strip().splitlines()[-60:])
                lines.append("```")
                lines.append("")
    lines.append("SMOKE-VERDICT: " + ("GREEN" if all_ok else "RED"))
    lines.append(f"END-MARKER-SMOKE-{time.strftime('%Y%m%d-%H%M%S')}")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    for ok, summary in steps:
        print(("[PASS] " if ok else "[FAIL] ") + summary)
    print()
    print("Verdict: " + ("GREEN" if all_ok else "RED"))
    # Machine-readable line on stdout too (the report file has its own copy);
    # automation (e.g. the knowledge.smoke_test agent tool) parses this.
    print("SMOKE-VERDICT: " + ("GREEN" if all_ok else "RED"))
    print(f"Report: {REPORT_PATH}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
