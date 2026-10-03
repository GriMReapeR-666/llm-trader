import json
import os
import re
import subprocess
import pytest
from bs4 import BeautifulSoup

BUNDLE_HTML_PATH = "visualizer/llm_trader_knowledge_studio.html"
PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"


# --- 1. Bundle File Existence & Size Threshold ---

def test_bundle_file_exists_and_size():
    """Verify visualizer/llm_trader_knowledge_studio.html exists and is larger than 150 KB."""
    assert os.path.exists(BUNDLE_HTML_PATH), f"Bundle file {BUNDLE_HTML_PATH} must exist"
    file_size_bytes = os.path.getsize(BUNDLE_HTML_PATH)
    file_size_kb = file_size_bytes / 1024
    assert file_size_kb > 150, f"Bundle file size is {file_size_kb:.1f} KB, expected > 150 KB for full standalone offline app"


# --- 2. Zero External Network Requests (100% Offline Integrity) ---

def test_bundle_zero_external_network_calls():
    """Verify bundle contains zero external CDN scripts, stylesheets, fonts, or network URLs."""
    assert os.path.exists(BUNDLE_HTML_PATH)
    with open(BUNDLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Disallow external script src
    external_scripts = re.findall(r'<script[^>]+src=["\'](https?:)?//[^"\']+["\']', html, re.IGNORECASE)
    assert len(external_scripts) == 0, f"Found forbidden external script tags: {external_scripts}"

    # Disallow external stylesheet links
    external_links = re.findall(r'<link[^>]+href=["\'](https?:)?//[^"\']+["\']', html, re.IGNORECASE)
    assert len(external_links) == 0, f"Found forbidden external link tags: {external_links}"

    # Disallow external @import in CSS
    external_imports = re.findall(r'@import\s+["\']?(https?:)?//[^"\'\s;]+', html, re.IGNORECASE)
    assert len(external_imports) == 0, f"Found forbidden external CSS @import rules: {external_imports}"

    # Verify standard HTML5 doctype and meta tags
    assert "<!DOCTYPE html>" in html or "<!doctype html>" in html.lower()
    assert 'charset="UTF-8"' in html or 'charset="utf-8"' in html


# --- 3. Critical JavaScript Symbols & Constructors ---

def test_bundle_critical_symbols_present():
    """Verify all 6 core classes, managers, and factories are present in the inlined scripts."""
    assert os.path.exists(BUNDLE_HTML_PATH)
    with open(BUNDLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    required_symbols = [
        "window.GRAPH_DATA",
        "CanvasEngine",
        "HUDControls",
        "initHUD",
        "DeepDiveDrawer",
        "initDeepDiveDrawer",
        "LifecycleTourManager",
        "initLifecycleTour",
        "KnowledgeMatrixManager",
        "initKnowledgeMatrix",
        "window.onerror",
    ]

    for sym in required_symbols:
        assert sym in html, f"Missing critical symbol '{sym}' in standalone bundle"


# --- 4. Complete Dataset Integrity Inside Inlined Payload ---

def test_bundle_dataset_integrity():
    """Verify inlined window.GRAPH_DATA contains all 12 domains, 109 nodes, the full payload edge set, and 7 lifecycle stages."""
    assert os.path.exists(BUNDLE_HTML_PATH)
    with open(BUNDLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Extract JSON string assigned to window.GRAPH_DATA
    match = re.search(r'window\.GRAPH_DATA\s*=\s*(\{[\s\S]*?\})\s*;\s*</script>', html)
    assert match is not None, "Failed to locate window.GRAPH_DATA assignment in HTML"

    payload_str = match.group(1)
    data = json.loads(payload_str)

    assert "subsystems" in data, "Payload must contain subsystems list"
    assert len(data["subsystems"]) == 12, f"Expected 12 subsystems, got {len(data['subsystems'])}"

    assert "nodes" in data, "Payload must contain nodes array"
    assert len(data["nodes"]) == 109, f"Expected exactly 109 nodes, got {len(data['nodes'])}"

    assert "edges" in data, "Payload must contain edges array"
    with open(PAYLOAD_PATH, "r", encoding="utf-8") as f:
        source_payload = json.load(f)
    assert len(data["edges"]) == len(source_payload["edges"]), (
        "Bundle edges ({}) must match payload edges ({})".format(
            len(data["edges"]), len(source_payload["edges"])
        )
    )
    assert "lifecycle_stages" in data, "Payload must contain lifecycle_stages"
    assert len(data["lifecycle_stages"]) == 7, f"Expected exactly 7 lifecycle stages, got {len(data['lifecycle_stages'])}"


# --- 5. Headless DOM Verification ---

def test_bundle_headless_dom_elements():
    """Verify presence of required element IDs specified in the Task 7 brief using BeautifulSoup."""
    assert os.path.exists(BUNDLE_HTML_PATH)
    with open(BUNDLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")

    required_ids = [
        # Main Canvas Stage & Minimap Radar
        "main-stage",
        "graph-canvas",
        "radar-hud",
        "radar-canvas",
        # Top Command Bar & Search Autocomplete
        "top-command-bar",
        "search",
        "search-results",
        # Deep-Dive Slide-Over Drawer & Dual-Lens Toggle
        "deep-dive-drawer",
        "lens-toggle",
        # 7-Stage Trade Lifecycle Tour HUD
        "tour-controls-bar",
        "tour-narrative-card",
        # Searchable Knowledge Matrix & Encyclopedia
        "knowledge-matrix-view",
        "matrix-grid",
        # Error Boundary Banner
        "error-boundary-banner",
    ]

    for el_id in required_ids:
        el = soup.find(id=el_id)
        assert el is not None, f"Required DOM element with id='{el_id}' not found in bundle HTML"


# --- 6. Inlined CSS Styling & Glassmorphism Verification ---

def test_bundle_inlined_styles():
    """Verify visualizer/src/styles.css is inlined with OLED dark palette and glassmorphism rules."""
    assert os.path.exists(BUNDLE_HTML_PATH)
    with open(BUNDLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    soup = BeautifulSoup(html, "html.parser")
    style_tag = soup.find("style")
    assert style_tag is not None, "HTML must contain an inlined <style> tag"

    css = style_tag.string or ""
    assert "#080c14" in css, "CSS must define dark OLED palette #080c14"
    assert "backdrop-filter" in css, "CSS must define frosted glassmorphism backdrop-filter"
    assert ".top-command-bar" in css, "CSS must include .top-command-bar styling"
    assert ".deep-dive-drawer" in css, "CSS must include .deep-dive-drawer styling"
    assert ".tour-controls-bar" in css, "CSS must include .tour-controls-bar styling"
    assert ".knowledge-matrix-view" in css, "CSS must include .knowledge-matrix-view styling"


# --- 7. Headless JavaScript Execution & Bootstrapping Smoke Test ---

def test_bundle_headless_js_execution():
    """Verify JavaScript executes cleanly in headless Node.js environment without runtime exceptions."""
    assert os.path.exists(BUNDLE_HTML_PATH)
    with open(BUNDLE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Extract the script content from the HTML bundle
    script_matches = re.findall(r'<script>([\s\S]*?)</script>', html, re.IGNORECASE)
    assert len(script_matches) > 0, "No inline <script> tags found in bundle HTML"

    full_script = "\n".join(script_matches)

    node_harness = f"""
    // Mock minimal browser globals
    class MockElement {{
        constructor(tagName, id = '', className = '') {{
            this.tagName = (tagName || 'DIV').toUpperCase();
            this.id = id;
            this.className = className;
            this.style = {{}};
            this.children = [];
            this.parentNode = null;
            this.attributes = new Map();
            this.listeners = new Map();
        }}
        setAttribute(k, v) {{ this.attributes.set(k, String(v)); if (k==='id') this.id = v; }}
        getAttribute(k) {{ return this.attributes.get(k) || null; }}
        hasAttribute(k) {{ return this.attributes.has(k); }}
        appendChild(ch) {{ ch.parentNode = this; this.children.push(ch); return ch; }}
        addEventListener(ev, fn) {{
            if (!this.listeners.has(ev)) this.listeners.set(ev, []);
            this.listeners.get(ev).push(fn);
        }}
        dispatchEvent(e) {{
            const arr = this.listeners.get(e.type || e) || [];
            for (const fn of arr) fn(e);
        }}
        querySelector(s) {{ return this.children.find(c => '#' + c.id === s || '.' + c.className === s) || null; }}
        querySelectorAll(s) {{ return this.children.filter(c => '#' + c.id === s || '.' + c.className === s); }}
        getContext() {{
            return {{
                clearRect: () => {{}},
                fillRect: () => {{}},
                strokeRect: () => {{}},
                beginPath: () => {{}},
                arc: () => {{}},
                fill: () => {{}},
                stroke: () => {{}},
                moveTo: () => {{}},
                lineTo: () => {{}},
                fillText: () => {{}},
                measureText: () => ({{ width: 50 }}),
                save: () => {{}},
                restore: () => {{}},
                scale: () => {{}},
                translate: () => {{}},
                setLineDash: () => {{}},
            }};
        }}
    }}

    class MockDocument {{
        constructor() {{
            this.body = new MockElement('BODY', 'body');
            this.head = new MockElement('HEAD', 'head');
            this.elements = new Map();
            this.listeners = new Map();
        }}
        createElement(tag) {{ return new MockElement(tag); }}
        getElementById(id) {{
            if (this.elements.has(id)) return this.elements.get(id);
            const el = new MockElement('DIV', id);
            this.elements.set(id, el);
            return el;
        }}
        querySelector(s) {{
            if (s.startsWith('#')) return this.getElementById(s.slice(1));
            return this.body.querySelector(s);
        }}
        querySelectorAll(s) {{ return this.body.querySelectorAll(s); }}
        addEventListener(ev, fn) {{
            if (!this.listeners.has(ev)) this.listeners.set(ev, []);
            this.listeners.get(ev).push(fn);
        }}
        dispatchEvent(e) {{
            const arr = this.listeners.get(e.type || e) || [];
            for (const fn of arr) fn(e);
        }}
    }}

    global.document = new MockDocument();
    global.window = global;
    global.window.document = global.document;
    global.window.devicePixelRatio = 1;
    global.window.innerWidth = 1920;
    global.window.innerHeight = 1080;
    global.window.requestAnimationFrame = (fn) => setTimeout(fn, 16);
    global.window.cancelAnimationFrame = (id) => clearTimeout(id);

    // Pre-populate document with required IDs
    const elIds = [
        'main-stage', 'graph-canvas', 'radar-hud', 'radar-canvas',
        'top-command-bar', 'search', 'search-results',
        'deep-dive-drawer', 'lens-toggle',
        'tour-controls-bar', 'tour-narrative-card',
        'knowledge-matrix-view', 'matrix-grid', 'matrix-card-grid',
        'error-boundary-banner'
    ];
    for (const id of elIds) {{
        const el = global.document.getElementById(id);
        global.document.body.appendChild(el);
    }}

    // Evaluate bundled script
    try {{
        {full_script}
        
        // Trigger DOMContentLoaded
        global.document.dispatchEvent({{ type: 'DOMContentLoaded' }});

        // Verify key globals
        if (!global.window.GRAPH_DATA) throw new Error('window.GRAPH_DATA missing');
        if (typeof global.CanvasEngine !== 'function') throw new Error('CanvasEngine missing');
        if (typeof global.initHUD !== 'function') throw new Error('initHUD missing');
        if (typeof global.initDeepDiveDrawer !== 'function') throw new Error('initDeepDiveDrawer missing');
        if (typeof global.initLifecycleTour !== 'function') throw new Error('initLifecycleTour missing');
        if (typeof global.initKnowledgeMatrix !== 'function') throw new Error('initKnowledgeMatrix missing');

        console.log('BUNDLE_HEADLESS_EXECUTION_OK');
    }} catch (err) {{
        console.error('BUNDLE_EXEC_ERROR:', err);
        process.exit(1);
    }}
    """

    res = subprocess.run(["node"], input=node_harness, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0, f"Headless execution failed:\n{res.stderr}\n{res.stdout}"
    assert "BUNDLE_HEADLESS_EXECUTION_OK" in res.stdout
