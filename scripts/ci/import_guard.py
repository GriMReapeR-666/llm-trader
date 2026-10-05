"""Fail if more repo modules are loaded twice than the ratchet allows.

The code puts core/, data/, intelligence/, ... on sys.path and imports the
same file as both `core.x` and `x`, which creates two module objects with
separate singletons and locks (audit finding P1-C1). Phase 3 removes this;
until then the count may only go down.

    python scripts/ci/import_guard.py [--max 25]
"""
import argparse
import collections
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def duplicated_modules():
    os.environ.setdefault("FAST_TEST", "1")
    os.environ.setdefault("LLM_TRADER_OPENBB_OFFLINE", "1")
    logging.disable(logging.CRITICAL)
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    import mt5_trader  # noqa: F401  (module import only; main() is not called)
    import ui.gui_api  # noqa: F401
    by_file = collections.defaultdict(list)
    for name, module in list(sys.modules.items()):
        if name in ("__main__", "__mp_main__"):
            continue
        path = getattr(module, "__file__", None)
        if path and os.path.abspath(path).startswith(ROOT + os.sep) and "site-packages" not in path and f"{os.sep}.venv{os.sep}" not in path:
            by_file[os.path.relpath(os.path.abspath(path), ROOT)].append(name)
    return {f: sorted(n) for f, n in by_file.items() if len(n) > 1}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args(argv)
    dups = duplicated_modules()
    for path, names in sorted(dups.items()):
        print(f"  {path}: {', '.join(names)}")
    print(f"modules loaded twice: {len(dups)} (ratchet: {a.max})")
    if len(dups) > a.max:
        print("FAIL: new double-loaded module; import it as package.module", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
