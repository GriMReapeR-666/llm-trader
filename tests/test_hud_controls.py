import json
import os
import subprocess
import pytest

STYLES_CSS_PATH = "visualizer/src/styles.css"
HUD_CONTROLS_JS_PATH = "visualizer/src/hud_controls.js"
PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"
CANVAS_ENGINE_JS_PATH = "visualizer/src/canvas_engine.js"


# --- 1. File Existence & Structure ---

def test_hud_files_exist():
    """Verify visualizer/src/styles.css and visualizer/src/hud_controls.js exist."""
    assert os.path.exists(STYLES_CSS_PATH), f"File {STYLES_CSS_PATH} must exist"
    assert os.path.exists(HUD_CONTROLS_JS_PATH), f"File {HUD_CONTROLS_JS_PATH} must exist"


# --- 2. CSS Styling Verification ---

def test_styles_css_palette_and_glassmorphism():
    """Verify cyber-quant OLED dark palette, glassmorphism, and status indicators in styles.css."""
    assert os.path.exists(STYLES_CSS_PATH)
    with open(STYLES_CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    # OLED dark palette
    assert "#080c14" in css, "Must contain OLED backdrop #080c14"
    assert "#0d1527" in css, "Must contain canvas stage #0d1527"
    assert "#0f172a" in css, "Must contain glass panel backdrop #0f172a"

    # Glassmorphic properties
    assert "backdrop-filter" in css and "blur(" in css, "Must contain backdrop-filter: blur(...)"
    assert "-webkit-backdrop-filter" in css, "Must include vendor prefix -webkit-backdrop-filter"
    assert "rgba(255, 255, 255," in css, "Must contain translucent border or accent using rgba white"
    assert "box-shadow" in css, "Must define layered glassmorphic shadows"

    # Status indicator colors
    assert "#10b981" in css, "Must define Emerald green #10b981 (Incoming Callers / Active)"
    assert "#f43f5e" in css, "Must define Rose red #f43f5e (Outgoing Dependencies / Gated)"
    assert "#a855f7" in css, "Must define Violet #a855f7 (Theoretical Grounding)"
    assert "#f59e0b" in css or "#facc15" in css, "Must define Amber/Gold (#f59e0b or #facc15)"
    assert "#06b6d4" in css or "#38bdf8" in css, "Must define Cyan/Sky Blue (#06b6d4 or #38bdf8)"
    assert "#ec4899" in css or "#f472b6" in css, "Must define Pink (#ec4899 or #f472b6)"


def test_styles_css_component_classes():
    """Verify required layout classes, HUD elements, tooltip, and scrollbars in styles.css."""
    with open(STYLES_CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    required_selectors = [
        ".top-command-bar",
        ".brand-badge",
        ".status-tag",
        ".perspective-switcher",
        ".perspective-btn",
        ".layout-selector",
        ".layout-btn",
        ".search-container",
        "#quick-search-input",
        "#search-results-dropdown",
        ".search-result-item",
        "#subsystem-filter-panel",
        ".subsystem-item",
        ".subsystem-checkbox",
        ".solo-btn",
        "#active-dependency-legend",
        ".legend-pill",
        "#node-hover-tooltip",
        "::-webkit-scrollbar",
        "@keyframes",
    ]

    for sel in required_selectors:
        assert sel in css, f"Missing required CSS selector or rule: {sel}"


# --- 3. JavaScript Syntax & Export Verification ---

def test_hud_controls_js_syntax():
    """Verify visualizer/src/hud_controls.js is valid ES6 syntax checked by node."""
    assert os.path.exists(HUD_CONTROLS_JS_PATH)
    res = subprocess.run(["node", "--check", HUD_CONTROLS_JS_PATH], capture_output=True, text=True)
    assert res.returncode == 0, f"JavaScript syntax error in {HUD_CONTROLS_JS_PATH}:\n{res.stderr}"


def test_hud_controls_exports_and_methods():
    """Verify initHUD and HUDControls are exported for both CommonJS and Browser."""
    node_script = """
    const hudModule = require('./visualizer/src/hud_controls.js');
    if (typeof hudModule.initHUD !== 'function') {
        throw new Error('initHUD must be an exported function');
    }
    if (typeof hudModule.fuzzySearch !== 'function') {
        throw new Error('fuzzySearch must be an exported helper function');
    }
    console.log('EXPORTS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node export verification failed:\n{res.stderr}\n{res.stdout}"
    assert "EXPORTS_OK" in res.stdout


# --- 4. Fuzzy Search Algorithm Verification ---

def test_fuzzy_search_logic():
    """Verify fuzzy search algorithm matches node ID, name, subsystem, summary, and code path."""
    node_script = """
    const { fuzzySearch } = require('./visualizer/src/hud_controls.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');
    const nodes = payload.nodes;

    // 1. Search by exact or partial ID
    const res1 = fuzzySearch(nodes, 'mt5_trader', 5);
    if (res1.length === 0 || !res1[0].id.includes('mt5')) {
        throw new Error('Search by ID failed: ' + JSON.stringify(res1.map(r => r.id)));
    }

    // 2. Search by Domain / Subsystem keyword
    const res2 = fuzzySearch(nodes, 'Circuit Breaker', 5);
    if (res2.length === 0) {
        throw new Error('Search by domain/keyword failed');
    }

    // 3. Search by Code Path keyword
    const res3 = fuzzySearch(nodes, 'macro_fundamental_pipeline', 5);
    if (res3.length === 0) {
        throw new Error('Search by code file path failed');
    }

    // 4. Search by ELI5 Summary keyword
    const res4 = fuzzySearch(nodes, 'deflated sharpe', 5);
    if (res4.length === 0) {
        throw new Error('Search by summary keyword failed');
    }

    // 5. Empty query returns empty or default
    const resEmpty = fuzzySearch(nodes, '', 5);
    if (resEmpty.length !== 0) {
        throw new Error('Empty query should return empty array');
    }

    console.log('FUZZY_SEARCH_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Fuzzy search test failed:\n{res.stderr}\n{res.stdout}"
    assert "FUZZY_SEARCH_OK" in res.stdout


# --- 5. Full HUD Lifecycle, DOM Mounting, and Event Listeners ---

def test_hud_controls_mounting_and_interactions():
    """Verify HUD mounts elements, binds search, layout, perspective, filter, and tooltip handlers."""
    node_script = """
    // Minimal DOM Mock environment for headless testing
    class MockElement {
        constructor(tagName, id = '', className = '') {
            this.tagName = tagName.toUpperCase();
            this.id = id;
            this.classList = {
                _classes: new Set(className ? className.split(/\\s+/).filter(Boolean) : []),
                add: (c) => this.classList._classes.add(c),
                remove: (c) => this.classList._classes.delete(c),
                contains: (c) => this.classList._classes.has(c),
                toggle: (c, force) => {
                    if (force !== undefined) {
                        if (force) this.classList._classes.add(c);
                        else this.classList._classes.delete(c);
                        return force;
                    }
                    if (this.classList._classes.has(c)) {
                        this.classList._classes.delete(c);
                        return false;
                    } else {
                        this.classList._classes.add(c);
                        return true;
                    }
                }
            };
            this.style = {};
            this.attributes = new Map();
            this.children = [];
            this.parentNode = null;
            this._innerHTML = '';
            this._textContent = '';
            this.value = '';
            this.checked = true;
            this.indeterminate = false;
            this.listeners = new Map();
        }

        get className() {
            return Array.from(this.classList._classes).join(' ');
        }
        set className(val) {
            this.classList._classes = new Set((val || '').split(/\\s+/).filter(Boolean));
        }

        get textContent() {
            if (this._textContent) return this._textContent;
            if (this._innerHTML) return this._innerHTML.replace(/<[^>]+>/g, '').trim();
            return this.children.map(c => c.textContent).join(' ');
        }
        set textContent(v) {
            this._textContent = String(v);
        }

        get innerHTML() {
            return this._innerHTML || '';
        }
        set innerHTML(v) {
            this._innerHTML = String(v);
        }

        setAttribute(k, v) {
            this.attributes.set(k, String(v));
            if (k === 'id') this.id = String(v);
            if (k === 'class') this.className = String(v);
        }
        getAttribute(k) { return this.attributes.get(k) || null; }
        hasAttribute(k) { return this.attributes.has(k); }
        removeAttribute(k) { this.attributes.delete(k); }

        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            return child;
        }

        removeChild(child) {
            const idx = this.children.indexOf(child);
            if (idx >= 0) {
                this.children.splice(idx, 1);
                child.parentNode = null;
            }
            return child;
        }

        addEventListener(event, fn) {
            if (!this.listeners.has(event)) this.listeners.set(event, []);
            this.listeners.get(event).push(fn);
        }

        removeEventListener(event, fn) {
            if (!this.listeners.has(event)) return;
            const arr = this.listeners.get(event);
            const idx = arr.indexOf(fn);
            if (idx >= 0) arr.splice(idx, 1);
        }

        dispatchEvent(e) {
            const arr = this.listeners.get(e.type) || [];
            for (const fn of arr) fn(e);
        }

        contains(el) {
            let curr = el;
            while (curr) {
                if (curr === this) return true;
                curr = curr.parentNode;
            }
            return false;
        }

        querySelector(sel) {
            const all = this.querySelectorAll(sel);
            return all.length > 0 ? all[0] : null;
        }

        querySelectorAll(sel) {
            const results = [];
            const match = (el) => {
                if (sel.startsWith('#') && el.id === sel.slice(1)) return true;
                if (sel.startsWith('.') && el.classList.contains(sel.slice(1))) return true;
                if (sel.startsWith('[data-') && sel.endsWith(']')) {
                    const attr = sel.slice(1, -1).split('=');
                    const key = attr[0];
                    const val = attr[1] ? attr[1].replace(/['"]/g, '') : null;
                    if (val !== null) return el.getAttribute(key) === val;
                    return el.hasAttribute(key);
                }
                if (el.tagName.toLowerCase() === sel.toLowerCase()) return true;
                return false;
            };

            const traverse = (el) => {
                for (const ch of el.children) {
                    if (match(ch)) results.push(ch);
                    traverse(ch);
                }
            };
            traverse(this);
            return results;
        }

        focus() { this._focused = true; }
        blur() { this._focused = false; }
    }

    class MockDocument {
        constructor() {
            this.body = new MockElement('body');
            this.listeners = new Map();
        }

        createElement(tagName) {
            return new MockElement(tagName);
        }

        getElementById(id) {
            return this.body.querySelector('#' + id);
        }

        querySelector(sel) {
            return this.body.querySelector(sel);
        }

        querySelectorAll(sel) {
            return this.body.querySelectorAll(sel);
        }

        addEventListener(event, fn) {
            if (!this.listeners.has(event)) this.listeners.set(event, []);
            this.listeners.get(event).push(fn);
        }

        dispatchEvent(e) {
            const arr = this.listeners.get(e.type) || [];
            for (const fn of arr) fn(e);
        }
    }

    // Set up global browser mocks
    global.document = new MockDocument();
    global.window = {
        document: global.document,
        addEventListener: (e, fn) => global.document.addEventListener(e, fn),
        dispatchEvent: (e) => global.document.dispatchEvent(e),
    };

    const { initHUD } = require('./visualizer/src/hud_controls.js');
    const CanvasEngine = require('./visualizer/src/canvas_engine.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    // Instantiate mock CanvasEngine
    const engine = new CanvasEngine({ data: payload });
    let selectedViaHUD = null;
    let focusedNodeId = null;
    engine.focusNode = (id) => { focusedNodeId = id; };
    engine.selectNode = (id) => { selectedViaHUD = id; };

    // Initialize HUD
    const hud = initHUD(engine, payload, global.document.body);

    // 1. Verify Top Command Bar Elements
    const topBar = global.document.querySelector('.top-command-bar');
    if (!topBar) throw new Error('Missing .top-command-bar');

    const brandBadge = topBar.querySelector('.brand-badge');
    if (!brandBadge) throw new Error('Missing .brand-badge');

    const statusTag = topBar.querySelector('.status-tag');
    if (!statusTag || !statusTag.textContent.includes('V22')) {
        throw new Error('Missing or invalid .status-tag');
    }

    // 2. Verify Perspective Mode Buttons
    const perspectiveBtns = topBar.querySelectorAll('.perspective-btn');
    if (perspectiveBtns.length < 4) {
        throw new Error('Expected at least 4 perspective buttons (code, quant, tour, matrix)');
    }
    const quantBtn = topBar.querySelector('[data-perspective="quant"]');
    if (!quantBtn) throw new Error('Missing [data-perspective="quant"] button');
    quantBtn.dispatchEvent({ type: 'click' });
    if (!quantBtn.classList.contains('active')) {
        throw new Error('Clicking perspective button did not set active class');
    }

    // 3. Verify Layout Selector Buttons
    const layoutBtns = topBar.querySelectorAll('.layout-btn');
    if (layoutBtns.length !== 5) {
        throw new Error('Expected exactly 5 layout buttons, got ' + layoutBtns.length);
    }
    const radarBtn = topBar.querySelector('[data-layout="radar"]');
    if (!radarBtn) throw new Error('Missing [data-layout="radar"] button');
    radarBtn.dispatchEvent({ type: 'click' });
    if (engine.currentLayout !== 'radar') {
        throw new Error('Clicking radar layout button did not update engine layout');
    }

    // 4. Verify Global Search & Shortcuts
    const searchInput = global.document.querySelector('#quick-search-input');
    const searchDropdown = global.document.querySelector('#search-results-dropdown');
    if (!searchInput || !searchDropdown) {
        throw new Error('Missing search input or dropdown');
    }

    // Test shortcut: '/' focuses search input
    global.document.dispatchEvent({ type: 'keydown', key: '/', target: global.document.body, preventDefault: () => {} });
    if (!searchInput._focused) {
        throw new Error('Shortcut "/" did not focus search input');
    }

    // Test typing search query
    searchInput.value = 'mt5_trader';
    searchInput.dispatchEvent({ type: 'input', target: searchInput });
    const results = searchDropdown.querySelectorAll('.search-result-item');
    if (results.length === 0) {
        throw new Error('Search input did not populate dropdown items');
    }

    // Test arrow key navigation down & Enter selection
    searchInput.dispatchEvent({ type: 'keydown', key: 'ArrowDown', preventDefault: () => {} });
    searchInput.dispatchEvent({ type: 'keydown', key: 'Enter', preventDefault: () => {} });
    if (!focusedNodeId || !focusedNodeId.includes('mt5_trader')) {
        throw new Error('Enter key on search item did not focus node in engine: ' + focusedNodeId);
    }

    // 5. Verify Subsystem Filter Checklist
    const filterPanel = global.document.querySelector('#subsystem-filter-panel');
    if (!filterPanel) throw new Error('Missing #subsystem-filter-panel');

    const checkboxes = filterPanel.querySelectorAll('.subsystem-checkbox');
    if (checkboxes.length !== 12) {
        throw new Error('Expected 12 subsystem checkboxes, got ' + checkboxes.length);
    }

    // Uncheck first subsystem
    const firstCheckbox = checkboxes[0];
    const subId = parseInt(firstCheckbox.getAttribute('data-subsystem-id'), 10);
    firstCheckbox.checked = false;
    firstCheckbox.dispatchEvent({ type: 'change', target: firstCheckbox });
    if (!engine.hiddenSubsystems || !engine.hiddenSubsystems.has(subId)) {
        throw new Error('Unchecking subsystem did not add to engine.hiddenSubsystems');
    }

    // Test Solo Button
    const soloBtns = filterPanel.querySelectorAll('.solo-btn');
    if (soloBtns.length !== 12) {
        throw new Error('Expected 12 solo buttons, got ' + soloBtns.length);
    }
    const targetSolo = soloBtns[1]; // solo subsystem 2
    const targetSoloId = parseInt(targetSolo.getAttribute('data-subsystem-id'), 10);
    targetSolo.dispatchEvent({ type: 'click' });
    if (engine.hiddenSubsystems.has(targetSoloId) || engine.hiddenSubsystems.size !== 11) {
        throw new Error('Solo button failed to isolate domain. Hidden count: ' + engine.hiddenSubsystems.size);
    }

    // 6. Verify Active Dependency Legend HUD
    const legend = global.document.querySelector('#active-dependency-legend');
    if (!legend) throw new Error('Missing #active-dependency-legend');
    const legendPills = legend.querySelectorAll('.legend-pill');
    if (legendPills.length < 3) {
        throw new Error('Expected at least 3 legend pills (Incoming, Outgoing, Grounding)');
    }

    // 7. Verify Hover Card Tooltip
    const tooltip = global.document.querySelector('#node-hover-tooltip');
    if (!tooltip) throw new Error('Missing #node-hover-tooltip');
    
    // Simulate hover on node
    const testNode = payload.nodes[0];
    if (typeof engine.onNodeHover === 'function') {
        engine.onNodeHover(testNode);
        if (tooltip.style.display === 'none' || tooltip.classList.contains('hidden')) {
            throw new Error('Hovering node did not make tooltip visible');
        }
        if (!tooltip.innerHTML.includes(testNode.name) && !tooltip.textContent.includes(testNode.name)) {
            throw new Error('Tooltip did not render node name');
        }

        // Simulate hover off
        engine.onNodeHover(null);
        if (tooltip.style.display !== 'none' && !tooltip.classList.contains('hidden')) {
            throw new Error('Hovering null did not hide tooltip');
        }
    }

    console.log('HUD_INTERACTIONS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"HUD interaction test failed:\n{res.stderr}\n{res.stdout}"
    assert "HUD_INTERACTIONS_OK" in res.stdout
