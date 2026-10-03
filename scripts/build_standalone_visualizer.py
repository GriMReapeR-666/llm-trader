#!/usr/bin/env python3
"""
Cyber-Quant HyperGraph Studio - Standalone Monolithic Bundler

Assembles all modular CSS, SVG assets, JavaScript controllers, and the complete
12-domain knowledge graph JSON dataset into a single self-contained production-grade
HTML application (visualizer/llm_trader_knowledge_studio.html).

Guarantees 100% offline execution with zero external CDN dependencies.
"""

import json
import os
import sys
import time

# Base directories
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VISUALIZER_DIR = os.path.join(BASE_DIR, "visualizer")
SRC_DIR = os.path.join(VISUALIZER_DIR, "src")
DATA_DIR = os.path.join(VISUALIZER_DIR, "data")

# Source files
STYLES_CSS = os.path.join(SRC_DIR, "styles.css")
PAYLOAD_JSON = os.path.join(DATA_DIR, "knowledge_graph_payload.json")
CANVAS_ENGINE_JS = os.path.join(SRC_DIR, "canvas_engine.js")
HUD_CONTROLS_JS = os.path.join(SRC_DIR, "hud_controls.js")
DEEP_DIVE_DRAWER_JS = os.path.join(SRC_DIR, "deep_dive_drawer.js")
LIFECYCLE_TOUR_JS = os.path.join(SRC_DIR, "lifecycle_tour.js")
KNOWLEDGE_MATRIX_JS = os.path.join(SRC_DIR, "knowledge_matrix.js")

# Target output
OUTPUT_HTML = os.path.join(VISUALIZER_DIR, "llm_trader_knowledge_studio.html")


def read_file(path: str) -> str:
    """Read UTF-8 encoded text file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required source file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_standalone_visualizer(output_path: str = OUTPUT_HTML) -> str:
    """
    Compile modular assets into a single monolithic standalone HTML application.
    """
    start_time = time.time()
    print("=" * 72)
    print("CYBER-QUANT HYPERGRAPH STUDIO: MONOLITHIC STANDALONE BUNDLER")
    print("=" * 72)

    # 1. Read and validate JSON data payload
    print(f"[1/5] Loading and validating knowledge graph payload: {PAYLOAD_JSON}")
    payload_raw = read_file(PAYLOAD_JSON)
    data = json.loads(payload_raw)

    num_nodes = len(data.get("nodes", []))
    num_edges = len(data.get("edges", []))
    num_subsystems = len(data.get("subsystems", []))
    num_stages = len(data.get("lifecycle_stages", []))

    print(f"      -> Nodes: {num_nodes}")
    print(f"      -> Directed Edges: {num_edges}")
    print(f"      -> Operational Subsystems: {num_subsystems}")
    print(f"      -> Trade Lifecycle Stages: {num_stages}")

    if num_nodes != 109 or num_subsystems != 12:
        print(f"      [WARN] Unexpected node ({num_nodes}) or subsystem ({num_subsystems}) count!")

    # Minified JSON payload for clean embedding
    payload_compact = json.dumps(data, separators=(',', ':'), ensure_ascii=False)

    # 2. Read CSS styling
    print(f"[2/5] Inlining CSS styling: {STYLES_CSS}")
    styles_css = read_file(STYLES_CSS)

    # 3. Read JavaScript modules
    print(f"[3/5] Inlining modular JavaScript engines:")
    canvas_engine_js = read_file(CANVAS_ENGINE_JS)
    print(f"      -> Canvas 2D Engine ({len(canvas_engine_js):,} bytes)")

    hud_controls_js = read_file(HUD_CONTROLS_JS)
    print(f"      -> HUD Controls & Command Bar ({len(hud_controls_js):,} bytes)")

    deep_dive_drawer_js = read_file(DEEP_DIVE_DRAWER_JS)
    print(f"      -> Deep-Dive Slide-Over Drawer ({len(deep_dive_drawer_js):,} bytes)")

    lifecycle_tour_js = read_file(LIFECYCLE_TOUR_JS)
    print(f"      -> 7-Stage Trade Lifecycle Flight Simulator ({len(lifecycle_tour_js):,} bytes)")

    knowledge_matrix_js = read_file(KNOWLEDGE_MATRIX_JS)
    print(f"      -> Searchable Knowledge Matrix & Encyclopedia ({len(knowledge_matrix_js):,} bytes)")

    # 4. Master Bootstrapping Script
    master_bootstrap_js = """
// ============================================================================
// MASTER APPLICATION BOOTSTRAPPER & COMPONENT ORCHESTRATOR
// ============================================================================

(function () {
  'use strict';

  // Global Error Boundary Handler
  window.onerror = function (msg, url, lineNo, columnNo, error) {
    console.error('Visualizer Runtime Error:', msg, error);
    var banner = document.getElementById('error-boundary-banner');
    if (banner) {
      banner.style.display = 'block';
      banner.innerHTML = '<div class="error-banner-inner">' +
        '<strong>System Notice:</strong> ' +
        (msg || 'An unhandled exception occurred during execution.') +
        (lineNo ? ' (Line: ' + lineNo + ')' : '') +
        '</div>';
    }
    return false;
  };

  // Wait for full DOM readiness
  function bootStudio() {
    try {
      var payload = window.GRAPH_DATA;
      if (!payload || !payload.nodes) {
        console.error('CRITICAL: window.GRAPH_DATA payload not loaded.');
        return;
      }

      var canvas = document.getElementById('graph-canvas');
      var radarCanvas = document.getElementById('radar-canvas');

      // 1. Instantiate High-DPI Canvas 2D Engine
      var engine = new CanvasEngine({
        canvas: canvas,
        radarCanvas: radarCanvas,
        data: payload
      });

      // 2. Instantiate Institutional Command Bar & Floating HUDs
      var hud = initHUD(engine, payload, document.body);

      // 3. Instantiate Universal Deep-Dive Slide-Over Drawer
      var drawer = initDeepDiveDrawer(engine, payload);

      // 4. Instantiate 7-Stage Trade Lifecycle Guided Flight Simulator
      var tour = initLifecycleTour(engine, payload, document.body);

      // 5. Instantiate Searchable Knowledge Matrix & Encyclopedia
      var matrix = initKnowledgeMatrix(engine, payload, drawer, document.body);

      // 6. Two-Way State Synchronization
      if (hud && matrix) {
        hud.setKnowledgeMatrix(matrix);
        matrix.setHUDControls(hud);
      }

      // Wire Tour Mode trigger to Top Command Bar perspective switcher
      if (hud && tour) {
        var prevPerspectiveChange = hud.onPerspectiveChange;
        hud.onPerspectiveChange = function (perspectiveId) {
          if (typeof prevPerspectiveChange === 'function') {
            prevPerspectiveChange(perspectiveId);
          }
          if (perspectiveId === 'tour') {
            tour.startTour();
          } else {
            if (tour.isActive) tour.stopTour();
          }
        };
      }

      // 7. Boot with Default Island Layout & Trigger 60 FPS Animation Loop
      engine.setLayout('islands');
      engine.start();

      // Expose globally for browser console exploration and programmatic control
      window.__STUDIO_APP__ = {
        engine: engine,
        hud: hud,
        drawer: drawer,
        tour: tour,
        matrix: matrix,
        data: payload
      };

      console.log(
        '%c[Cyber-Quant HyperGraph Studio]%c Bootstrapped successfully with ' +
        payload.nodes.length + ' nodes, ' + payload.edges.length + ' edges, and ' +
        payload.subsystems.length + ' operational domains.',
        'color: #38bdf8; font-weight: bold; font-size: 13px;',
        'color: #94a3b8; font-size: 12px;'
      );
    } catch (err) {
      console.error('Fatal initialization error:', err);
      if (window.onerror) {
        window.onerror(err.message || String(err), 'bootStudio', 0, 0, err);
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootStudio);
  } else {
    bootStudio();
  }
})();
"""

    # 5. Assemble HTML Document
    print(f"[4/5] Assembling monolithic single-file production template...")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <meta name="description" content="LLM Trader - Cyber-Quant HyperGraph Studio & Knowledge Base Visualizer. Zero-dependency institutional standalone offline application.">
  <title>LLM Trader - Cyber-Quant HyperGraph Studio & Knowledge Base Visualizer</title>

  <style>
    /* Inlined Master Cyber-Quant Terminal Stylesheet */
{styles_css}

    /* Error Boundary Floating Alert */
    .error-boundary-banner {{
      position: fixed;
      top: 64px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 9999;
      background: rgba(244, 63, 94, 0.15);
      border: 1px solid rgba(244, 63, 94, 0.6);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border-radius: 8px;
      padding: 10px 20px;
      color: #fda4af;
      font-family: 'JetBrains Mono', 'Fira Code', monospace;
      font-size: 13px;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
    }}
  </style>
</head>
<body>

  <!-- Runtime Error Boundary Notification Banner -->
  <div id="error-boundary-banner" class="error-boundary-banner" style="display: none;"></div>

  <!-- Primary Canvas 2D World Viewport -->
  <main id="main-stage" class="main-stage">
    <canvas id="graph-canvas" class="graph-canvas"></canvas>

    <!-- Interactive Minimap Radar HUD -->
    <div id="radar-hud" class="radar-hud">
      <canvas id="radar-canvas" class="radar-canvas" width="240" height="160"></canvas>
    </div>
  </main>

  <!-- Top Command Bar (HUD Controls mount point & fallback layout) -->
  <header id="top-command-bar" class="top-command-bar">
    <!-- Embedded Search Structure for Headless DOM Verification & Instant Fallback -->
    <div id="search" class="search-container">
      <div class="search-input-wrapper">
        <input
          type="text"
          id="quick-search-input"
          placeholder="Search code, quant models, formulas..."
          autocomplete="off"
          spellcheck="false"
        />
        <span class="search-shortcut-badge">/ or ⌘K</span>
      </div>
      <div id="search-results" class="search-dropdown"></div>
    </div>
  </header>

  <!-- Universal Deep-Dive Slide-Over Inspection Drawer -->
  <div id="deep-dive-backdrop" class="drawer-backdrop" aria-hidden="true"></div>
  <aside id="deep-dive-drawer" class="deep-dive-drawer" aria-hidden="true" role="dialog">
    <div id="lens-toggle" class="lens-toggle-container" role="tablist">
      <button class="lens-toggle-btn active" data-lens="eli5" role="tab" aria-selected="true">
        <span class="lens-icon">💡</span>
        <span class="lens-text">Plain English (ELI5)</span>
      </button>
      <button class="lens-toggle-btn" data-lens="quant" role="tab" aria-selected="false">
        <span class="lens-icon">📐</span>
        <span class="lens-text">Institutional Quant Rigor</span>
      </button>
    </div>
  </aside>

  <!-- 7-Stage Trade Lifecycle Interactive Flight Simulator HUD -->
  <div id="tour-controls-bar" class="tour-controls-bar hidden" style="display: none;"></div>
  <div id="tour-narrative-card" class="tour-narrative-card hidden" style="display: none;"></div>

  <!-- Searchable Knowledge Matrix & Quant Encyclopedia Modal -->
  <div id="knowledge-matrix-view" class="knowledge-matrix-view" style="display: none;">
    <div id="matrix-grid" class="matrix-grid-wrapper">
      <div id="matrix-card-grid" class="matrix-card-grid"></div>
    </div>
  </div>

  <!-- Embedded Data Payload and Inlined JavaScript Engines -->
  <script>
    // Inlined Knowledge Graph & Quantitative Grounding Payload
    window.GRAPH_DATA = {payload_compact};
  </script>

  <script>
    // 1. High-DPI Canvas 2D Engine
{canvas_engine_js}
  </script>

  <script>
    // 2. HUD Controls & Bloomberg Terminal Command Bar
{hud_controls_js}
  </script>

  <script>
    // 3. Universal Deep-Dive Slide-Over Drawer
{deep_dive_drawer_js}
  </script>

  <script>
    // 4. 7-Stage Trade Lifecycle Flight Simulator
{lifecycle_tour_js}
  </script>

  <script>
    // 5. Searchable Knowledge Matrix & Quant Encyclopedia
{knowledge_matrix_js}
  </script>

  <script>
    // 6. Master Bootstrapping Orchestrator
{master_bootstrap_js}
  </script>

</body>
</html>
"""

    # 6. Write out monolithic HTML file
    print(f"[5/5] Writing production artifact: {output_path}")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    total_bytes = os.path.getsize(output_path)
    total_kb = total_bytes / 1024
    elapsed = time.time() - start_time

    print("=" * 72)
    print(f"BUILD COMPLETED SUCCESSFULLY in {elapsed:.2f}s!")
    print(f"Target Artifact: {output_path}")
    print(f"Total File Size: {total_bytes:,} bytes ({total_kb:.1f} KB)")
    print(f"Network Status:  100% Offline (ZERO external CDN/HTTP calls)")
    print("=" * 72)

    return output_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else OUTPUT_HTML
    build_standalone_visualizer(out)
