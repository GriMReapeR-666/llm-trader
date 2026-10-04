"""Verify single import root and prevent subpackage sys.path pollution (P1-C1).

1. Asserts scripts/ci/import_guard.py reports 0 double-loaded modules.
2. AST check asserting that no production file adds a subpackage folder to sys.path.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROD_PACKAGES = ["core", "data", "intelligence", "operations", "research", "ui", "strategies"]


def test_import_guard_reports_zero_duplicates():
    """Verify that importing mt5_trader and ui.gui_api results in 0 duplicated modules."""
    script = ROOT / "scripts" / "ci" / "import_guard.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--max", "0"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"import_guard failed:\n{proc.stdout}\n{proc.stderr}"
    assert "modules loaded twice: 0" in proc.stdout


def test_no_production_sys_path_subpackage_inserts():
    """AST check: no production file may add subpackage folders to sys.path."""
    subpackages = set(PROD_PACKAGES)
    violations = []

    prod_files = []
    # Production packages
    for pkg in PROD_PACKAGES:
        pkg_dir = ROOT / pkg
        if pkg_dir.is_dir():
            prod_files.extend(pkg_dir.rglob("*.py"))
    # Repo root production files
    for f in ROOT.glob("*.py"):
        prod_files.append(f)

    for py_file in prod_files:
        try:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Look for sys.path.insert or sys.path.append
                func = node.func
                is_sys_path = False
                if isinstance(func, ast.Attribute) and func.attr in ("insert", "append"):
                    val = func.value
                    if isinstance(val, ast.Attribute) and val.attr == "path":
                        if isinstance(val.value, ast.Name) and val.value.id in ("sys", "_sys"):
                            is_sys_path = True

                if is_sys_path:
                    # Check arguments for any string literal or path referring to subpackages
                    for arg in node.args:
                        # Direct string literal
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            if any(sub in arg.value.split(os.sep) for sub in subpackages):
                                violations.append((py_file.relative_to(ROOT), node.lineno, arg.value))
                        # os.path.join or / operator
                        for sub_node in ast.walk(arg):
                            if isinstance(sub_node, ast.Constant) and isinstance(sub_node.value, str):
                                if sub_node.value in subpackages:
                                    violations.append((py_file.relative_to(ROOT), node.lineno, sub_node.value))

    assert not violations, f"Found production sys.path subpackage inserts: {violations}"
