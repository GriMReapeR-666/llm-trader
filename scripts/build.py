# Unified Quant Trader Project Build & Quality Verification
# Combines:
# 1. RTK (Rust Token Killer) optimized test and typecheck execution
# 2. Graphify AST Knowledge Graph synchronizer
# 3. Ponytail lazy senior dev simplicity & debt audit
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_cmd(cmd, cwd=ROOT, allow_retry=False):
    print(f"\n>> {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=cwd)
    if res.returncode != 0 and allow_retry:
        print(f"Retrying once after brief pause (handling Windows file locks): {cmd}")
        import time
        time.sleep(2)
        # Clean any lingering temporary file in graphify-out
        tmp = os.path.join(ROOT, "graphify-out", ".graph.tmp.json")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        res = subprocess.run(cmd, shell=True, cwd=cwd)
    if res.returncode != 0:
        print(f"FAILED (exit code {res.returncode}): {cmd}")
        sys.exit(res.returncode)


def main():
    print("======================================================================")
    print("      UNIFIED BUILD PIPELINE: RTK + GRAPHIFY + PONYTAIL              ")
    print("=======================================================================")
    
    # 1. Ponytail Simplicity & Debt Audit
    audit_script = os.path.join(ROOT, "scripts", "ponytail_audit.py")
    run_cmd(f'python "{audit_script}"')
    
    # 2. TypeScript & OpenTUI Build with RTK
    print("\n[STEP 2/3] OpenTUI Typecheck & Selftest (via RTK)...")
    tui_dir = os.path.join(ROOT, "tui_opentui")
    run_cmd("rtk bun run typecheck", cwd=tui_dir)
    run_cmd("rtk bun run selftest", cwd=tui_dir)
    
    # 3. Graphify Knowledge Graph Synchronizer
    print("\n[STEP 3/3] Graphify AST Knowledge Graph Update...")
    run_cmd("rtk graphify update .", cwd=ROOT, allow_retry=True)
    
    print("\n======================================================================")
    print("      ALL BUILD, KNOWLEDGE GRAPH & SIMPLICITY GATES PASSED            ")
    print("======================================================================")


if __name__ == "__main__":
    main()
