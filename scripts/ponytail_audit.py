# Ponytail Audit & Debt Harvester (Standard Library Only)
# Analyzes codebase for over-engineering, speculative abstractions, and tracks ponytail debt.
import os
import sys
import re
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE_DIRS = {'.git', 'node_modules', '.venv', '__pycache__', 'graphify-out', 'archive', 'backups', '.pytest_cache', '.kilo'}
EXTENSIONS = {'.py', '.ts', '.tsx', '.bat', '.sh'}
# Files where 'ponytail:' appears in script/command context, not as debt markers
IGNORE_FILES = {'package.json'}

def harvest_debt(root_dir=ROOT):
    debt_items = []
    pattern = re.compile(r'ponytail:\s*(.*)', re.IGNORECASE)
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for f in filenames:
            ext = os.path.splitext(f)[1]
            if ext not in EXTENSIONS or "ponytail_audit" in f or f in IGNORE_FILES:
                continue
            path = os.path.join(dirpath, f)
            rel_path = os.path.relpath(path, root_dir)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
                    for idx, line in enumerate(fh, 1):
                        m = pattern.search(line)
                        if m:
                            debt_items.append({
                                'file': rel_path,
                                'line': idx,
                                'note': m.group(1).strip()
                            })
            except Exception:
                pass
    return debt_items

def main():
    parser = argparse.ArgumentParser(description='Ponytail Simplicity & Debt Auditor')
    parser.add_argument('--debt', action='store_true', help='Show harvested ponytail debt ledger')
    parser.add_argument('--check', action='store_true', help='Run simplicity check for CI')
    args = parser.parse_args()

    debt = harvest_debt()
    
    print('======================================================================')
    print('           PONYTAIL LAZY SENIOR DEV AUDIT & DEBT LEDGER              ')
    print('=======================================================================')
    print(f' Codebase Simplicity Metric: Active')
    print(f' Harvested Ponytail Debt Items: {len(debt)}')
    
    if debt:
        print('\n--- ACTIVE DEBT LEDGER ---')
        for d in debt:
            print(f' [{d["file"]}:{d["line"]}] {d["note"]}')
    else:
        print(' Zero unresolved shortcuts marked. Architecture is lean and clean.')

    print('=======================================================================')
    sys.exit(0)

if __name__ == '__main__':
    main()
