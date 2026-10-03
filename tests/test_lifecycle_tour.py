import json
import os
import subprocess
import pytest

STYLES_CSS_PATH = "visualizer/src/styles.css"
LIFECYCLE_TOUR_JS_PATH = "visualizer/src/lifecycle_tour.js"
HUD_CONTROLS_JS_PATH = "visualizer/src/hud_controls.js"
CANVAS_ENGINE_JS_PATH = "visualizer/src/canvas_engine.js"
PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"


# --- 1. File Existence & CSS Selectors ---

def test_tour_files_exist():
    """Verify visualizer/src/lifecycle_tour.js and visualizer/src/styles.css exist."""
    assert os.path.exists(STYLES_CSS_PATH), f"File {STYLES_CSS_PATH} must exist"
    assert os.path.exists(LIFECYCLE_TOUR_JS_PATH), f"File {LIFECYCLE_TOUR_JS_PATH} must exist"


def test_styles_css_tour_classes():
    """Verify lifecycle tour CSS rules exist for controls bar, narrative card, stage dots, and animations."""
    with open(STYLES_CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    required_selectors = [
        "#tour-controls-bar",
        ".tour-controls-bar",
        ".tour-btn",
        ".tour-play-btn",
        ".tour-stage-dots",
        ".tour-stage-dot",
        ".tour-speed-select",
        ".tour-exit-btn",
        "#tour-narrative-card",
        ".tour-narrative-card",
        ".tour-stage-badge",
        ".tour-timestamp",
        ".tour-narrative-text",
        ".tour-active-chips",
        ".tour-chip",
    ]

    for sel in required_selectors:
        assert sel in css, f"Missing required CSS selector in styles.css: {sel}"

    # Glassmorphism and animations
    assert "backdrop-filter" in css, "Tour controls must feature glassmorphic backdrop-filter"
    assert "fixed" in css or "absolute" in css, "Tour HUD must use fixed or absolute positioning"
    assert "border-radius" in css, "Tour controls must have rounded corners"


# --- 2. JavaScript Syntax & Universal Export Verification ---

def test_lifecycle_tour_js_syntax():
    """Verify visualizer/src/lifecycle_tour.js is valid ES6 syntax checked by node."""
    assert os.path.exists(LIFECYCLE_TOUR_JS_PATH)
    res = subprocess.run(["node", "--check", LIFECYCLE_TOUR_JS_PATH], capture_output=True, text=True)
    assert res.returncode == 0, f"JavaScript syntax error in {LIFECYCLE_TOUR_JS_PATH}:\n{res.stderr}"


def test_lifecycle_tour_exports():
    """Verify lifecycle_tour.js exports LifecycleTourManager and initLifecycleTour."""
    node_script = """
    const tourModule = require('./visualizer/src/lifecycle_tour.js');
    if (typeof tourModule.initLifecycleTour !== 'function') {
        throw new Error('initLifecycleTour must be an exported function');
    }
    if (typeof tourModule.LifecycleTourManager !== 'function') {
        throw new Error('LifecycleTourManager class must be exported');
    }
    console.log('EXPORTS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Tour export verification failed:\n{res.stderr}\n{res.stdout}"
    assert "EXPORTS_OK" in res.stdout


# --- 3. 7 Execution Stages Structure & Data Fallback ---

def test_lifecycle_tour_seven_stages():
    """Verify LifecycleTourManager defines and initializes exactly 7 execution stages with complete metadata."""
    node_script = """
    const { LifecycleTourManager } = require('./visualizer/src/lifecycle_tour.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    // Test with payload
    const manager = new LifecycleTourManager(null, payload);
    const stages = manager.stages;

    if (!Array.isArray(stages) || stages.length !== 7) {
        throw new Error('Expected exactly 7 stages, got: ' + (stages ? stages.length : 'none'));
    }

    const expectedTitles = [
        'Market Data & Macro Ingestion',
        'Macro & Microstructure Regime Filter',
        'Alpha Mining & Factor Discovery',
        'Multi-Agent LLM Deliberation',
        'Quantitative Risk & Overfit Gating',
        'Dual-Velocity MT5 Order Routing',
        'Memory Persistence & EWC Meta-Loop'
    ];

    stages.forEach((stage, idx) => {
        if (!stage.name || typeof stage.name !== 'string') {
            throw new Error(`Stage ${idx + 1} missing name`);
        }
        if (!Array.isArray(stage.active_nodes) || stage.active_nodes.length === 0) {
            throw new Error(`Stage ${idx + 1} (${stage.name}) must have non-empty active_nodes`);
        }
        if (!stage.narrative || typeof stage.narrative !== 'string') {
            throw new Error(`Stage ${idx + 1} missing narrative explanation`);
        }
        if (typeof stage.duration_ms !== 'number' || stage.duration_ms <= 0) {
            throw new Error(`Stage ${idx + 1} missing positive duration_ms`);
        }
    });

    console.log('STAGES_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Stage definition test failed:\n{res.stderr}\n{res.stdout}"
    assert "STAGES_OK" in res.stdout


# --- 4. Playback Controller & State Machine ---

def test_lifecycle_tour_playback_state_machine():
    """Verify playback controls: start, pause, resume, next, prev, jump to stage, speed control, stop."""
    node_script = """
    const { LifecycleTourManager } = require('./visualizer/src/lifecycle_tour.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    // Minimal mock CanvasEngine
    const mockEngine = {
        glideTo: (x, y, zoom) => {},
        activeStageIndex: null,
        activeStageNodeIds: new Set(),
        activeStageEdges: [],
        resetCamera: () => {},
        nodeMap: new Map(payload.nodes.map(n => [n.id, n]))
    };

    const manager = new LifecycleTourManager(mockEngine, payload);

    // Initial state
    if (manager.currentStageIndex !== 0) throw new Error('Initial stage index should be 0');
    if (manager.isPlaying !== false) throw new Error('Initial isPlaying should be false');

    // Start Tour
    manager.startTour();
    if (!manager.isActive) throw new Error('startTour() should set isActive to true');
    if (!manager.isPlaying) throw new Error('startTour() should start playing');

    // Pause / Play toggle
    manager.togglePlayPause();
    if (manager.isPlaying !== false) throw new Error('togglePlayPause() should pause');
    manager.togglePlayPause();
    if (manager.isPlaying !== true) throw new Error('togglePlayPause() should resume');

    // Next Stage
    manager.nextStage();
    if (manager.currentStageIndex !== 1) throw new Error('nextStage() should advance to stage 1');

    // Previous Stage
    manager.prevStage();
    if (manager.currentStageIndex !== 0) throw new Error('prevStage() should return to stage 0');

    // Jump to specific stage
    manager.goToStage(4);
    if (manager.currentStageIndex !== 4) throw new Error('goToStage(4) should jump to stage 4');

    // Speed selection
    manager.setSpeed(2.0);
    if (manager.speed !== 2.0) throw new Error('setSpeed(2.0) should set speed to 2.0');
    manager.setSpeed(0.5);
    if (manager.speed !== 0.5) throw new Error('setSpeed(0.5) should set speed to 0.5');

    // Stop Tour
    manager.stopTour();
    if (manager.isActive !== false) throw new Error('stopTour() should set isActive to false');
    if (manager.isPlaying !== false) throw new Error('stopTour() should pause playback');

    console.log('STATE_MACHINE_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Playback state machine test failed:\n{res.stderr}\n{res.stdout}"
    assert "STATE_MACHINE_OK" in res.stdout


# --- 5. Dynamic Camera Glide, Active Node Illumination & Edge Stimulation ---

def test_lifecycle_tour_camera_and_illumination():
    """Verify tour glides camera to center active nodes, sets active stage node IDs, and stimulates edge pulses."""
    node_script = """
    const { LifecycleTourManager } = require('./visualizer/src/lifecycle_tour.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    let glidedX = null;
    let glidedY = null;
    let glidedZoom = null;

    const mockEngine = {
        glideTo: (x, y, zoom) => {
            glidedX = x;
            glidedY = y;
            glidedZoom = zoom;
        },
        activeStageIndex: null,
        activeStageNodeIds: new Set(),
        activeStageEdges: [],
        edges: payload.edges.map(e => ({ ...e })),
        nodeMap: new Map(payload.nodes.map(n => [n.id, { ...n, x: n.x_island || 100, y: n.y_island || 100 }]))
    };

    const manager = new LifecycleTourManager(mockEngine, payload);

    // Go to Stage 3 (Alpha Mining & Factor Discovery)
    manager.goToStage(2); // 0-indexed stage 3

    // 1. Verify Camera Glide called
    if (glidedX === null || glidedY === null || typeof glidedZoom !== 'number') {
        throw new Error('goToStage did not call canvasEngine.glideTo with valid coordinates');
    }

    // 2. Verify Active Stage Nodes Set
    if (!mockEngine.activeStageNodeIds || mockEngine.activeStageNodeIds.size === 0) {
        throw new Error('goToStage did not populate engine.activeStageNodeIds');
    }

    const currentStage = manager.stages[2];
    for (const nodeId of currentStage.active_nodes) {
        if (!mockEngine.activeStageNodeIds.has(nodeId)) {
            throw new Error(`Engine activeStageNodeIds missing node ${nodeId} from stage 3`);
        }
    }

    // 3. Verify Active Stage Edges Set & Edge pulse stimulated
    if (!mockEngine.activeStageEdges) {
        throw new Error('goToStage did not set engine.activeStageEdges');
    }

    // Stop tour cleans up highlights
    manager.stopTour();
    if (mockEngine.activeStageNodeIds.size !== 0) {
        throw new Error('stopTour() should clear activeStageNodeIds');
    }
    if (mockEngine.activeStageEdges.length !== 0) {
        throw new Error('stopTour() should clear activeStageEdges');
    }

    console.log('CAMERA_AND_ILLUMINATION_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Camera and illumination test failed:\n{res.stderr}\n{res.stdout}"
    assert "CAMERA_AND_ILLUMINATION_OK" in res.stdout


# --- 6. DOM UI Mounting, Narrative Card, & Interactive Controls ---

def test_lifecycle_tour_dom_mounting_and_interactions():
    """Verify Tour controls bar and narrative card mount into DOM, render badges/chips, and handle user clicks."""
    node_script = """
    // Minimal DOM Mock environment
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
            this.value = '1x';
            this.listeners = new Map();
        }

        get className() { return Array.from(this.classList._classes).join(' '); }
        set className(val) { this.classList._classes = new Set((val || '').split(/\\s+/).filter(Boolean)); }

        get textContent() {
            if (this._textContent) return this._textContent;
            if (this._innerHTML) return this._innerHTML.replace(/<[^>]+>/g, ' ').replace(/\\s+/g, ' ').trim();
            return this.children.map(c => c.textContent).join(' ');
        }
        set textContent(v) { this._textContent = String(v); }

        get innerHTML() { return this._innerHTML || ''; }
        set innerHTML(v) {
            this._innerHTML = String(v);
            this._parseBasicHTML(v);
        }

        _parseBasicHTML(html) {
            this.children = [];
            if (!html || typeof html !== 'string') return;

            const tokenRegex = /<\\/?([a-zA-Z0-9\\-]+)([^>]*)>|([^<]+)/g;
            const stack = [this];
            let match;

            while ((match = tokenRegex.exec(html)) !== null) {
                const fullTag = match[0];
                const tagName = match[1];
                const attrsStr = match[2];
                const text = match[3];

                if (text) {
                    const trimmed = text.trim();
                    if (trimmed && stack.length > 0) {
                        stack[stack.length - 1]._textContent = (stack[stack.length - 1]._textContent ? stack[stack.length - 1]._textContent + ' ' : '') + trimmed;
                    }
                } else if (fullTag.startsWith('</')) {
                    if (stack.length > 1 && stack[stack.length - 1].tagName === tagName.toUpperCase()) {
                        stack.pop();
                    }
                } else if (fullTag.endsWith('/>')) {
                    const el = new MockElement(tagName);
                    this._extractAttrs(el, attrsStr);
                    stack[stack.length - 1].appendChild(el);
                } else {
                    const el = new MockElement(tagName);
                    this._extractAttrs(el, attrsStr);
                    stack[stack.length - 1].appendChild(el);
                    stack.push(el);
                }
            }
        }

        _extractAttrs(el, attrsStr) {
            if (!attrsStr) return;
            const idMatch = attrsStr.match(/id="([^"]+)"/);
            if (idMatch) el.id = idMatch[1];
            const classMatch = attrsStr.match(/class="([^"]+)"/);
            if (classMatch) {
                classMatch[1].split(/\\s+/).forEach(c => el.classList.add(c));
            }
            const dataMatches = attrsStr.matchAll(/data-([a-zA-Z0-9\\-]+)="([^"]+)"/g);
            for (const dm of dataMatches) {
                el.setAttribute('data-' + dm[1], dm[2]);
            }
        }

        setAttribute(k, v) {
            this.attributes.set(k, String(v));
            if (k === 'id') this.id = String(v);
            if (k === 'class') {
                this.classList._classes = new Set(String(v).split(/\\s+/).filter(Boolean));
            }
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

        dispatchEvent(e) {
            const arr = this.listeners.get(e.type) || [];
            for (const fn of arr) fn(e);
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
    }

    class MockDocument {
        constructor() {
            this.body = new MockElement('body');
            this.listeners = new Map();
        }
        createElement(tagName) { return new MockElement(tagName); }
        getElementById(id) { return this.body.querySelector('#' + id); }
        querySelector(sel) { return this.body.querySelector(sel); }
        querySelectorAll(sel) { return this.body.querySelectorAll(sel); }
        addEventListener(event, fn) {
            if (!this.listeners.has(event)) this.listeners.set(event, []);
            this.listeners.get(event).push(fn);
        }
        dispatchEvent(e) {
            const arr = this.listeners.get(e.type) || [];
            for (const fn of arr) fn(e);
        }
    }

    global.document = new MockDocument();
    global.window = {
        document: global.document,
        addEventListener: (e, fn) => global.document.addEventListener(e, fn),
        dispatchEvent: (e) => global.document.dispatchEvent(e)
    };

    const { initLifecycleTour } = require('./visualizer/src/lifecycle_tour.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    const mockEngine = {
        glideTo: () => {},
        resetCamera: () => {},
        activeStageIndex: null,
        activeStageNodeIds: new Set(),
        activeStageEdges: [],
        edges: payload.edges.map(e => ({ ...e })),
        nodeMap: new Map(payload.nodes.map(n => [n.id, { ...n, x: 0, y: 0 }]))
    };

    const tour = initLifecycleTour(mockEngine, payload, global.document.body);

    // 1. Controls Bar & Narrative Card Existence
    const controlsBar = global.document.querySelector('#tour-controls-bar');
    if (!controlsBar) throw new Error('Missing #tour-controls-bar in DOM');

    const narrativeCard = global.document.querySelector('#tour-narrative-card');
    if (!narrativeCard) throw new Error('Missing #tour-narrative-card in DOM');

    // 2. Initial State Hidden until startTour()
    tour.startTour();

    if (controlsBar.style.display === 'none' || controlsBar.classList.contains('hidden')) {
        throw new Error('Controls bar should be visible after startTour()');
    }
    if (narrativeCard.style.display === 'none' || narrativeCard.classList.contains('hidden')) {
        throw new Error('Narrative card should be visible after startTour()');
    }

    // 3. Narrative Card Content Verification
    const stageBadge = narrativeCard.querySelector('.tour-stage-badge');
    if (!stageBadge || !stageBadge.textContent.includes('STAGE 01 / 07')) {
        throw new Error('Stage badge missing or incorrect: ' + (stageBadge ? stageBadge.textContent : 'none'));
    }

    const timestamp = narrativeCard.querySelector('.tour-timestamp');
    if (!timestamp || !timestamp.textContent.includes('ms')) {
        throw new Error('Timestamp indicator missing ms notation: ' + (timestamp ? timestamp.textContent : 'none'));
    }

    const narrativeText = narrativeCard.querySelector('.tour-narrative-text');
    if (!narrativeText || narrativeText.textContent.trim().length === 0) {
        throw new Error('Narrative explanation text should not be empty');
    }

    const chips = narrativeCard.querySelectorAll('.tour-chip');
    if (chips.length === 0) {
        throw new Error('Narrative card should render component chips for active nodes');
    }

    // 4. Interactive Stage Progress Dots
    const dots = controlsBar.querySelectorAll('.tour-stage-dot');
    if (dots.length !== 7) {
        throw new Error('Expected 7 stage navigation dots, got ' + dots.length);
    }

    // Click on Stage 4 dot
    dots[3].dispatchEvent({ type: 'click' });
    if (tour.currentStageIndex !== 3) {
        throw new Error('Clicking 4th dot did not set stage index to 3');
    }
    if (!dots[3].classList.contains('active')) {
        throw new Error('Clicked dot should have active class');
    }

    // 5. Play / Pause Button Click
    const playBtn = controlsBar.querySelector('.tour-play-btn');
    if (!playBtn) throw new Error('Missing .tour-play-btn');
    playBtn.dispatchEvent({ type: 'click' });
    if (tour.isPlaying !== false) {
        throw new Error('Clicking playBtn did not pause tour');
    }

    // 6. Exit Button Click
    const exitBtn = controlsBar.querySelector('.tour-exit-btn');
    if (!exitBtn) throw new Error('Missing .tour-exit-btn');
    exitBtn.dispatchEvent({ type: 'click' });
    if (tour.isActive !== false) {
        throw new Error('Clicking exitBtn did not exit tour');
    }

    console.log('DOM_INTERACTIONS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"DOM mounting and interactions test failed:\n{res.stderr}\n{res.stdout}"
    assert "DOM_INTERACTIONS_OK" in res.stdout


# --- 7. HUD Perspective Switcher Mode Integration ---

def test_hud_perspective_tour_integration():
    """Verify switching to 'tour' perspective in HUD controls triggers startTour(), and stopping reverts perspective."""
    node_script = """
    // Set up mock DOM
    class MockElement {
        constructor(tagName, id = '', className = '') {
            this.tagName = tagName.toUpperCase();
            this.id = id;
            this.classList = {
                _classes: new Set(className ? className.split(/\\s+/).filter(Boolean) : []),
                add: (c) => this.classList._classes.add(c),
                remove: (c) => this.classList._classes.delete(c),
                contains: (c) => this.classList._classes.has(c)
            };
            this.style = {};
            this.attributes = new Map();
            this.children = [];
            this.parentNode = null;
            this._innerHTML = '';
            this._textContent = '';
            this.listeners = new Map();
        }
        get textContent() { return this._textContent || this.children.map(c => c.textContent).join(' '); }
        set textContent(v) { this._textContent = String(v); }
        get innerHTML() { return this._innerHTML || ''; }
        set innerHTML(v) { this._innerHTML = String(v); }
        setAttribute(k, v) { this.attributes.set(k, String(v)); if (k === 'id') this.id = String(v); }
        getAttribute(k) { return this.attributes.get(k) || null; }
        hasAttribute(k) { return this.attributes.has(k); }
        appendChild(ch) {
            if (!ch) return;
            if (ch.parentNode) {
                const idx = ch.parentNode.children.indexOf(ch);
                if (idx >= 0) ch.parentNode.children.splice(idx, 1);
            }
            ch.parentNode = this;
            this.children.push(ch);
            return ch;
        }
        addEventListener(event, fn) {
            if (!this.listeners.has(event)) this.listeners.set(event, []);
            this.listeners.get(event).push(fn);
        }
        dispatchEvent(e) {
            const arr = this.listeners.get(e.type) || [];
            for (const fn of arr) fn(e);
        }
        querySelector(sel) { const all = this.querySelectorAll(sel); return all.length > 0 ? all[0] : null; }
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
                return el.tagName.toLowerCase() === sel.toLowerCase();
            };
            const visited = new Set();
            const traverse = (el) => {
                if (visited.has(el)) return;
                visited.add(el);
                for (const ch of el.children) {
                    if (match(ch)) results.push(ch);
                    traverse(ch);
                }
            };
            traverse(this);
            return results;
        }
    }

    class MockDocument {
        constructor() { this.body = new MockElement('body'); }
        createElement(t) { return new MockElement(t); }
        querySelector(s) { return this.body.querySelector(s); }
        querySelectorAll(s) { return this.body.querySelectorAll(s); }
        addEventListener() {}
    }

    global.document = new MockDocument();
    global.window = { document: global.document, addEventListener: () => {} };

    const { initHUD } = require('./visualizer/src/hud_controls.js');
    const { initLifecycleTour } = require('./visualizer/src/lifecycle_tour.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    const mockEngine = {
        glideTo: () => {},
        resetCamera: () => {},
        activeStageIndex: null,
        activeStageNodeIds: new Set(),
        activeStageEdges: [],
        edges: payload.edges.map(e => ({ ...e })),
        nodeMap: new Map(payload.nodes.map(n => [n.id, { ...n, x: 0, y: 0 }]))
    };

    const hud = initHUD(mockEngine, payload, global.document.body);
    const tour = initLifecycleTour(mockEngine, payload, global.document.body);

    // Wire perspective change callback
    hud.onPerspectiveChange = (mode) => {
        if (mode === 'tour') {
            tour.startTour();
        } else {
            tour.stopTour();
        }
    };

    // When tour stops, notify HUD to switch back to code
    tour.onTourExit = () => {
        if (typeof hud.setPerspective === 'function') {
            hud.setPerspective('code');
        }
    };

    // Click Tour perspective button
    const tourBtn = global.document.querySelector('[data-perspective="tour"]');
    if (!tourBtn) throw new Error('Missing [data-perspective="tour"] in top bar');

    tourBtn.dispatchEvent({ type: 'click' });
    if (!tour.isActive) {
        throw new Error('Clicking tour perspective button did not activate tour');
    }

    // Stop tour and verify onTourExit triggers
    tour.stopTour();
    if (hud.currentPerspective !== 'code') {
        throw new Error('Exiting tour did not revert HUD currentPerspective to code');
    }

    console.log('PERSPECTIVE_INTEGRATION_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"HUD perspective tour integration failed:\n{res.stderr}\n{res.stdout}"
    assert "PERSPECTIVE_INTEGRATION_OK" in res.stdout
