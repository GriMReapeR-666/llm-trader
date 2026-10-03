# Cyber-Quant HyperGraph Studio & Knowledge Base Visualizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a zero-dependency, ultra-fast standalone single-file HTML/JS/CSS application (`visualizer/llm_trader_knowledge_studio.html`) that delivers an institutional-grade, highly intuitive knowledge graph visualizer and comprehensive educational knowledge base for the LLM Trader codebase.

**Architecture:** A custom high-DPI 60 FPS HTML5 Canvas 2D rendering engine with spring-damped camera physics, ambient coordinate grid, 5 specialized topological layouts, and dynamic glowing signal pulses. Features a dual-lens Deep-Dive Drawer with an instant toggle between "Plain-English (ELI5)" and "Institutional Quant Rigor", a 7-stage interactive "Trade Lifecycle" flight simulator, and a searchable Knowledge Matrix encyclopedia.

**Tech Stack:** Vanilla JavaScript (ES6+), HTML5 Canvas 2D, SVG HUDs, CSS3 Glassmorphism, Embedded JSON Datasets, Zero external network dependencies.

**Spec:** `docs/superpowers/specs/2026-09-21-knowledge-graph-studio-design.md`

## Global Constraints

- Standalone single-file HTML (`visualizer/llm_trader_knowledge_studio.html`) with zero external network requests (100% offline, zero CDN calls).
- 60 FPS performance on standard laptop hardware using canvas double-buffering, viewport culling, and spatial hashing.
- Exact grounding in `mt5_trader.py`, `trading_knowledge_base/KNOWLEDGE_BASE_AND_CODE.md`, `quant_knowledge_framework/`, and `graphify-out/`.
- 12 color-coded architectural domains with complete 8-attribute schema on every node.

---

### Task 1: Comprehensive Data Extraction & Graph Dataset Generation

**Files:**
- Create: `scripts/generate_knowledge_graph_data.py`
- Create: `tests/test_knowledge_graph_data.py`
- Source: `graphify-out/graph.json`, `trading_knowledge_base/KNOWLEDGE_BASE_AND_CODE.md`, `trading_knowledge_base/modules/`, `mt5_trader.py`

**Interfaces:**
- Produces: `visualizer/data/knowledge_graph_payload.json` and inlined JS object `window.GRAPH_DATA` containing:
  - `nodes`: Array of `DeepKnowledgeNode` (12 subsystems, tiers L0-L3, ELI5 summaries, LaTeX equations, code snippets, inputs/outputs, failure modes, callers, dependencies, cross-links).
  - `edges`: Array of directed links (`source`, `target`, `type`, `label`).
  - `subsystems`: Metadata for all 12 operational domains.
  - `lifecycle_stages`: 7 trade execution stages with active node IDs and narrative cards.

- [ ] **Step 1: Write the failing test for data extraction and validation**

```python
# tests/test_knowledge_graph_data.py
import json
import os
import pytest

def test_knowledge_graph_payload_integrity():
    payload_path = "visualizer/data/knowledge_graph_payload.json"
    assert os.path.exists(payload_path), "Payload file must exist"
    with open(payload_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert "nodes" in data and len(data["nodes"]) > 100
    assert "edges" in data and len(data["edges"]) > 200
    assert "subsystems" in data and len(data["subsystems"]) == 12
    assert "lifecycle_stages" in data and len(data["lifecycle_stages"]) == 7
    
    # Check node schema
    for node in data["nodes"]:
        assert "id" in node
        assert "name" in node
        assert "subsystem_id" in node
        assert 1 <= node["subsystem_id"] <= 12
        assert "plain_english_summary" in node and len(node["plain_english_summary"]) > 10
        assert "data_contract" in node
        assert "callers" in node
        assert "dependencies" in node
```

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk pytest tests/test_knowledge_graph_data.py -v`
Expected: FAIL with "Payload file must exist"

- [ ] **Step 3: Implement data extraction script**

Create `scripts/generate_knowledge_graph_data.py` to parse `graphify-out/graph.json`, `trading_knowledge_base/KNOWLEDGE_BASE_AND_CODE.md`, and the 15 modules in `trading_knowledge_base/modules/`. Synthesize the 12 domains, 8-attribute schema, precompute coordinates for all 5 layouts (Islands, Pipeline, Radar, Lineage, Force), and export `visualizer/data/knowledge_graph_payload.json`.

- [ ] **Step 4: Run test to verify it passes**

Run: `rtk python scripts/generate_knowledge_graph_data.py`
Run: `rtk pytest tests/test_knowledge_graph_data.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
rtk git add scripts/generate_knowledge_graph_data.py tests/test_knowledge_graph_data.py visualizer/data/knowledge_graph_payload.json
rtk git commit -m "feat: compile complete knowledge graph and quant payload"
```

---

### Task 2: High-DPI Canvas 2D Engine, Camera Physics & Layout Simulators

**Files:**
- Create: `visualizer/src/canvas_engine.js`
- Create: `tests/test_canvas_math.py`

**Interfaces:**
- Consumes: `window.GRAPH_DATA`
- Produces: `CanvasEngine` class with methods:
  - `init(canvas, radarCanvas)`
  - `setDimensions(w, h)`
  - `worldToScreen(x, y)`, `screenToWorld(x, y)`
  - `computeLayout(mode)` ('islands', 'pipeline', 'radar', 'tree', 'force')
  - `render()`: 60 FPS animation loop with viewport culling, Bezier curves, animated edge signal pulses, node pills, and radar minimap.

- [ ] **Step 1: Write test for layout and math algorithms**

```python
# tests/test_canvas_math.py
import json

def test_layout_coordinates_exist():
    with open("visualizer/data/knowledge_graph_payload.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    for n in data["nodes"]:
        assert "x_island" in n and "y_island" in n
        assert "x_pipeline" in n and "y_pipeline" in n
        assert "x_radar" in n and "y_radar" in n
```

- [ ] **Step 2: Run test to verify it passes or fails**

Run: `rtk pytest tests/test_canvas_math.py -v`
Expected: PASS or FAIL depending on script output.

- [ ] **Step 3: Implement `canvas_engine.js`**

Implement smooth spring-lerp camera, high-DPI scaling (`window.devicePixelRatio`), edge signal pulse generator (`edge.pulseProgress += speed`), node collision pill drawing, and minimap radar frustum renderer.

- [ ] **Step 4: Verify canvas engine script syntax**

Run: `rtk node --check visualizer/src/canvas_engine.js` (or python validation script)
Expected: Clean check without syntax errors.

- [ ] **Step 5: Commit**

```bash
rtk git add visualizer/src/canvas_engine.js tests/test_canvas_math.py
rtk git commit -m "feat: implement high-dpi canvas engine and 5 topological layout modes"
```

---

### Task 3: Interactive Command Center, Floating Glass HUDs & Search Autocomplete

**Files:**
- Create: `visualizer/src/hud_controls.js`
- Create: `visualizer/src/styles.css`

**Interfaces:**
- Produces:
  - Top Glass Bar with brand badge, perspective switcher, layout selector, and fullscreen toggle.
  - Real-time search with fuzzy matching, dropdown cards, keyboard shortcuts (`/`, `Ctrl+K`), and instant camera lock.
  - Subsystem checklist filter with color indicators, counts, and solo toggle.
  - Hover Card Tooltip with frosted glass styling and live metrics.

- [ ] **Step 1: Implement CSS styling for cyber-quant terminal HUDs**

Create `visualizer/src/styles.css` with dark OLED palette (`#080c14`), frosted glass panels (`backdrop-filter: blur(16px)`), neon accents, pill badges, and scrollbar styling.

- [ ] **Step 2: Implement HUD event listeners and search autocomplete**

Create `visualizer/src/hud_controls.js` connecting search queries to `canvasEngine.focusNode(nodeId)`. Connect layout buttons to `canvasEngine.switchLayout(layoutName)`.

- [ ] **Step 3: Commit**

```bash
rtk git add visualizer/src/hud_controls.js visualizer/src/styles.css
rtk git commit -m "feat: add glassmorphic HUD controls, search autocomplete, and filter panel"
```

---

### Task 4: Universal Deep-Dive Slide-Over Drawer with ELI5 vs Quant Rigor

**Files:**
- Create: `visualizer/src/deep_dive_drawer.js`

**Interfaces:**
- Produces:
  - `openDrawer(nodeId)`: Slides out drawer from right edge.
  - Toggle between `ELI5` (analogy, intuitive purpose, loss prevention) and `Quant` (LaTeX math, algorithmic formulations, proofs).
  - Syntax-highlighted code viewer with 1-click copy.
  - Interactive clickable caller and callee tags (`← Incoming Callers` in Emerald green, `→ Outgoing Dependencies` in Rose red).
  - Cross-Link jump button connecting code files to financial theory nodes.
  - Failure mode & circuit breaker response card.

- [ ] **Step 1: Implement `deep_dive_drawer.js`**

Handle HTML rendering for all 8 node schema attributes, tab switching between ELI5 and Quant Rigor, code snippet copying, and cross-reference navigation.

- [ ] **Step 2: Commit**

```bash
rtk git add visualizer/src/deep_dive_drawer.js
rtk git commit -m "feat: implement universal deep-dive drawer with ELI5 and quant toggle"
```

---

### Task 5: 7-Stage Interactive "Trade Lifecycle" Guided Flight Simulator

**Files:**
- Create: `visualizer/src/lifecycle_tour.js`

**Interfaces:**
- Produces:
  - `LifecycleTourManager` controlling 7 stages:
    1. Market Data Ingestion
    2. Macro & Regime Filter
    3. Alpha Discovery & Factor Scoring
    4. Multi-Agent LLM Deliberation
    5. Quantitative Risk & Overfit Gating
    6. Dual-Velocity MT5 Order Routing
    7. Continual Memory & EWC Regularization
  - Interactive floating playback bar (Play/Pause, Prev, Next, Speed: 1x, 2x, 5x, Stage dots).
  - Automated camera glide tracking active stage nodes and streaming glowing pulse particles along connecting paths.
  - Microsecond narrative overlay detailing the live execution mechanics.

- [ ] **Step 1: Implement `lifecycle_tour.js`**

Implement state machine, stage timers, active node set illumination, edge pulse acceleration, and narrative card updates.

- [ ] **Step 2: Commit**

```bash
rtk git add visualizer/src/lifecycle_tour.js
rtk git commit -m "feat: add 7-stage interactive trade lifecycle flight simulator"
```

---

### Task 6: Searchable Knowledge Matrix & Encyclopedia Library Mode

**Files:**
- Create: `visualizer/src/knowledge_matrix.js`

**Interfaces:**
- Produces:
  - Fullscreen Knowledge Matrix modal/view.
  - Filterable catalog of all 15 core quantitative modules and major system god nodes.
  - Filter pills: By Domain (1..12), Complexity (Foundational, Intermediate, Advanced), Type (Code vs Theory).
  - Fast text search across formulas, titles, and definitions.
  - Card click action to focus directly in the graph canvas or open the deep-dive drawer.

- [ ] **Step 1: Implement `knowledge_matrix.js`**

Render responsive card grid with KaTeX math rendering, search filtering, and seamless graph focus links.

- [ ] **Step 2: Commit**

```bash
rtk git add visualizer/src/knowledge_matrix.js
rtk git commit -m "feat: add knowledge matrix and quant encyclopedia library mode"
```

---

### Task 7: Monolithic Standalone Bundler, Automated Smoke Tests & Final Polish

**Files:**
- Create: `scripts/build_standalone_visualizer.py`
- Create: `tests/test_standalone_bundle.py`
- Output: `visualizer/llm_trader_knowledge_studio.html`

**Interfaces:**
- Inlines all HTML, CSS, JavaScript, and JSON datasets into a single self-contained file.
- Guarantees zero external network requests.
- Validates bundle integrity and browser compatibility.

- [ ] **Step 1: Write bundle verification test**

```python
# tests/test_standalone_bundle.py
import os
import re

def test_standalone_bundle_integrity():
    html_path = "visualizer/llm_trader_knowledge_studio.html"
    assert os.path.exists(html_path), "Single-file bundle must exist"
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    assert "<!DOCTYPE html>" in content
    assert "GRAPH_DATA" in content
    assert "CanvasEngine" in content
    assert "LifecycleTourManager" in content
    assert "deep_dive_drawer" in content or "openDrawer" in content
    
    # Zero external CDN script tags
    external_scripts = re.findall(r'<script\s+src="http', content)
    assert len(external_scripts) == 0, "No external script tags allowed"
```

- [ ] **Step 2: Implement standalone bundler script**

Create `scripts/build_standalone_visualizer.py` that reads HTML template, bundles CSS, JS modules, and JSON data, and writes out `visualizer/llm_trader_knowledge_studio.html`.

- [ ] **Step 3: Run bundler and execute test**

Run: `rtk python scripts/build_standalone_visualizer.py`
Run: `rtk pytest tests/test_standalone_bundle.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
rtk git add scripts/build_standalone_visualizer.py tests/test_standalone_bundle.py visualizer/llm_trader_knowledge_studio.html
rtk git commit -m "feat: build production standalone single-file visualizer application"
```
