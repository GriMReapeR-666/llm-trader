#!/usr/bin/env python3
"""Codemod to migrate bare imports to packaged imports (PR 3.1, P1-C1).

Rewrites:
- `import X` -> `from <pkg> import X`
- `import X as Y` -> `from <pkg> import X as Y`
- `from X import ...` -> `from <pkg>.X import ...`
- String targets in tests (mock.patch, monkeypatch.setattr, importlib.import_module)
- Cleans up subpackage sys.path.insert / sys.path.append calls.

Usage:
    python scripts/codemods/package_imports.py [--dry-run] [--apply]
"""
import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOT_PACKAGES = ["core", "data", "intelligence", "operations", "research", "ui", "strategies"]

# Skip folders
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "backups", "archive", "build", "dist", ".kilo", ".opencode"}

def build_module_map():
    mod_to_pkg = {}
    for pkg in ROOT_PACKAGES:
        pkg_dir = ROOT / pkg
        if not pkg_dir.is_dir():
            continue
        for path in pkg_dir.rglob("*.py"):
            if path.name == "__init__.py":
                continue
            mod_name = path.stem
            if mod_name == "scenario_generator":
                # Known duplicate, always imported as full package
                continue
            rel_pkg = str(path.parent.relative_to(ROOT)).replace(os.sep, ".")
            mod_to_pkg[mod_name] = rel_pkg
    return mod_to_pkg

def clean_subpackage_sys_path(lines, file_rel_path):
    """Remove or sanitize subpackage sys.path inserts."""
    new_lines = []
    # Pattern matching sys.path insertion of a subpackage
    subpkg_re = re.compile(
        r'''sys\.path\.(insert|append)\s*\(\s*(?:0\s*,\s*)?'''
        r'''(?:os\.path\.join\([^)]*["'](core|data|intelligence|operations|research|ui|strategies)["']\)|'''
        r'''str\(Path\([^)]*\)\s*/\s*["'](core|data|intelligence|operations|research|ui|strategies)["']\)|'''
        r'''Path\([^)]*\)\s*/\s*["'](core|data|intelligence|operations|research|ui|strategies)["']|'''
        r'''os\.path\.join\(BASE_DIR,\s*["'](core|data|intelligence|operations|research|ui|strategies)["']\)|'''
        r'''os\.path\.join\(self\.base_dir,\s*["'](core|data|intelligence|operations|research|ui|strategies)["']\)|'''
        r'''os\.path\.join\(os\.path\.dirname\(BASE_DIR\),\s*["'](core|data|intelligence|operations|research|ui|strategies)["']\)|'''
        r'''os\.path\.join\(BASE_DIR,\s*["']\.\.["'],\s*["'](core|data|intelligence|operations|research|ui|strategies)["']\))'''
    )
    # UI BASE_DIR is .../ui
    ui_base_re = re.compile(r'''sys\.path\.insert\s*\(\s*0\s*,\s*BASE_DIR\s*\)''')
    ui_parent_re = re.compile(r'''sys\.path\.insert\s*\(\s*0\s*,\s*os\.path\.dirname\(BASE_DIR\)\s*\)''')

    for line in lines:
        stripped = line.strip()
        if subpkg_re.search(stripped):
            # Drop line
            continue
        if file_rel_path.startswith("ui/") and ui_base_re.match(stripped):
            # Drop line
            continue
        if file_rel_path.startswith("ui/") and ui_parent_re.match(stripped):
            # Drop line (repo root is already added at ui/gui_api.py top)
            continue
        new_lines.append(line)
    return new_lines

def rewrite_file(file_path, mod_to_pkg, apply_changes=False):
    rel_path = str(file_path.relative_to(ROOT))
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError:
        return 0

    lines = content.splitlines(keepends=True)
    replacements = []  # (start_line_1idx, end_line_1idx, new_text)

    # 1. AST imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # Check if any imported name is in mod_to_pkg
            for alias in node.names:
                if alias.name in mod_to_pkg and not "." in alias.name:
                    pkg = mod_to_pkg[alias.name]
                    # Get the line(s) for this import
                    start_line = node.lineno - 1
                    end_line = getattr(node, "end_lineno", node.lineno)
                    orig = "".join(lines[start_line:end_line])
                    # Preserve indentation
                    indent = orig[:len(orig) - len(orig.lstrip())]
                    as_clause = f" as {alias.asname}" if alias.asname else ""
                    new_text = f"{indent}from {pkg} import {alias.name}{as_clause}\n"
                    replacements.append((node.lineno, getattr(node, "end_lineno", node.lineno), new_text))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module in mod_to_pkg:
                pkg = mod_to_pkg[node.module]
                start_line = node.lineno - 1
                end_line = getattr(node, "end_lineno", node.lineno)
                orig = "".join(lines[start_line:end_line])
                # Replace "from <module>" with "from <pkg>.<module>"
                pattern = rf"(\bfrom\s+){re.escape(node.module)}(\b)"
                if re.search(pattern, orig):
                    new_text = re.sub(pattern, rf"\g<1>{pkg}.{node.module}\2", orig, count=1)
                    replacements.append((node.lineno, getattr(node, "end_lineno", node.lineno), new_text))

    if not replacements and not any("sys.path" in l for l in lines) and "patch(" not in content and "monkeypatch" not in content and "import_module(" not in content:
        return 0

    # Sort replacements by start line descending so line indices don't shift
    replacements.sort(key=lambda x: x[0], reverse=True)
    new_lines = list(lines)
    for start_1idx, end_1idx, new_text in replacements:
        new_lines[start_1idx - 1:end_1idx] = [new_text]

    # 2. String replacements in tests/
    new_content = "".join(new_lines)
    changed_str = False
    for mod_name, pkg in mod_to_pkg.items():
        # patch("mod_name.
        p1 = f'patch("{mod_name}.'
        r1 = f'patch("{pkg}.{mod_name}.'
        if p1 in new_content:
            new_content = new_content.replace(p1, r1)
            changed_str = True

        p1_sq = f"patch('{mod_name}."
        r1_sq = f"patch('{pkg}.{mod_name}."
        if p1_sq in new_content:
            new_content = new_content.replace(p1_sq, r1_sq)
            changed_str = True

        # monkeypatch.setattr("mod_name.
        mp1 = f'monkeypatch.setattr("{mod_name}.'
        mr1 = f'monkeypatch.setattr("{pkg}.{mod_name}.'
        if mp1 in new_content:
            new_content = new_content.replace(mp1, mr1)
            changed_str = True

        mp1_sq = f"monkeypatch.setattr('{mod_name}."
        mr1_sq = f"monkeypatch.setattr('{pkg}.{mod_name}."
        if mp1_sq in new_content:
            new_content = new_content.replace(mp1_sq, mr1_sq)
            changed_str = True

        # importlib.import_module("mod_name")
        im1 = f'importlib.import_module("{mod_name}")'
        ir1 = f'importlib.import_module("{pkg}.{mod_name}")'
        if im1 in new_content:
            new_content = new_content.replace(im1, ir1)
            changed_str = True

    # 3. Clean subpackage sys.path inserts
    final_lines = new_content.splitlines(keepends=True)
    cleaned_lines = clean_subpackage_sys_path(final_lines, rel_path)

    final_content = "".join(cleaned_lines)
    if final_content != content:
        if apply_changes:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(final_content)
        return len(replacements) + (1 if changed_str else 0)
    return 0

def main():
    apply_changes = "--apply" in sys.argv
    mod_to_pkg = build_module_map()
    print(f"Discovered {len(mod_to_pkg)} modules across packages {ROOT_PACKAGES}.")

    modified_files = 0
    total_changes = 0

    for root, dirs, files in os.walk(ROOT):
        # Prune skip dirs
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            # Don't rewrite this codemod script itself
            if path == Path(__file__).resolve():
                continue
            n = rewrite_file(path, mod_to_pkg, apply_changes=apply_changes)
            if n > 0:
                modified_files += 1
                total_changes += n
                rel = path.relative_to(ROOT)
                # print(f"  {'Modified' if apply_changes else 'Would modify'}: {rel} ({n} edits)")

    action = "Modified" if apply_changes else "Would modify"
    print(f"\n{action} {modified_files} files with {total_changes} rewrites.")
    if not apply_changes:
        print("Run with --apply to write changes.")

if __name__ == "__main__":
    main()
