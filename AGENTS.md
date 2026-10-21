# Project Engineering Standards: RTK, Graphify & Ponytail

This repository enforces a strict modern quant development standard combining **token efficiency**, **persistent knowledge graphs**, and *lazy senior developer minimalism*.

---

## 1. RTK (Rust Token Killer) — Token Optimization

- **Rule:** Always prefix verbose terminal and test commands with `rtk` (e.g. `rtk bun run selftest`, `rtk pytest`, `rtk git status`).
- **Goal:** Filters boilerplate outputs, saving up to 90% of context tokens during agentic workflows and developer interactions.
- **Commands:**
  - `rtk gain`: Review token savings scoreboard.
  - `rtk bun run build:rtk`: Token-optimized full build.

---

## 2. Graphify — Codebase Knowledge Graph

- **Rule:** The repository maintains an AST-level knowledge graph under `graphify-out/`.
- **Navigation Flow:**
  - Before writing code, inspect relationships using `graphify query "<topic>"` or `graphify path "<nodeA>" "<nodeB>"`.
  - Always run `graphify update .` (or `bun run graphify`) after modifying code files to keep the graph current.

---

## 3. Ponytail — LAzy Senior Dev Mode

- **Rule:** The best code is the code never written. Before adding code, ascend the simplicity ladder:
  1. **YAGNI:** Does this need to exist at all?
  2. **Reuse:** Does a utility already exist in `core/`, `operations/`, or `tui_opentui/`?
  3. **Standard Library First:** Avoid introducing third-party packages if Python or TypeScript stdlib provides it.
  4. **Shortest Working Diff:** Deletion over addition. Boring over clever. Fewest files possible.
- **Debt Tracking:**
  - Mark deliberate shortcuts with `ponytail: <ceiling and upgrade path>`.
  - Audit and harvest debt anytime using `python scripts/ponytail_audit.py --debt` or `bun run ponytail:debt`.

---

## 4. Unified Build Pipeline

Run the full pipeline via:
- Windows: Double-click `build.bat` or run `build.bat` in shell.
- Terminal: `python scripts/build.py`
- OpenTUI: `cd tui_opentui && bun run build:rtk`
