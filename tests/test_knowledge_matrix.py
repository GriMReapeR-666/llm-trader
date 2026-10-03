import json
import os
import subprocess
import pytest

STYLES_CSS_PATH = "visualizer/src/styles.css"
KNOWLEDGE_MATRIX_JS_PATH = "visualizer/src/knowledge_matrix.js"
HUD_CONTROLS_JS_PATH = "visualizer/src/hud_controls.js"
DEEP_DIVE_DRAWER_JS_PATH = "visualizer/src/deep_dive_drawer.js"
PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"


# --- 1. File Existence & CSS Selectors ---

def test_knowledge_matrix_files_exist():
    """Verify visualizer/src/knowledge_matrix.js and visualizer/src/styles.css exist."""
    assert os.path.exists(STYLES_CSS_PATH), f"File {STYLES_CSS_PATH} must exist"
    assert os.path.exists(KNOWLEDGE_MATRIX_JS_PATH), f"File {KNOWLEDGE_MATRIX_JS_PATH} must exist"


def test_styles_css_matrix_classes():
    """Verify knowledge matrix CSS rules exist for full-screen overlay, filter controls, card grid, and animations."""
    with open(STYLES_CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    required_selectors = [
        "#knowledge-matrix-view",
        ".knowledge-matrix-view",
        ".matrix-header",
        ".matrix-title",
        ".matrix-summary-badge",
        ".matrix-close-btn",
        ".matrix-filter-bar",
        "#matrix-search-input",
        ".matrix-search-wrapper",
        ".matrix-filter-group",
        ".matrix-filter-select",
        "#matrix-card-grid",
        ".matrix-card-grid",
        ".matrix-card",
        ".matrix-card-header",
        ".matrix-card-title",
        ".matrix-domain-badge",
        ".matrix-tier-badge",
        ".matrix-category-chip",
        ".matrix-card-summary",
        ".matrix-formula-chip",
        ".matrix-card-stats",
        ".matrix-card-actions",
        ".matrix-deep-dive-btn",
        ".matrix-focus-graph-btn",
    ]

    for sel in required_selectors:
        assert sel in css, f"Missing required CSS selector in styles.css: {sel}"

    # Visual design checks: Glassmorphism, grid, OLED backdrop, transitions
    assert "backdrop-filter" in css, "Knowledge matrix must feature glassmorphic backdrop-filter"
    assert "grid" in css, "Knowledge matrix cards must use grid display"
    assert "fixed" in css or "absolute" in css, "Knowledge matrix overlay must be fixed or absolute positioned"
    assert "transition" in css or "animation" in css, "Knowledge matrix must feature smooth transitions"


# --- 2. JavaScript Syntax & Universal Export Verification ---

def test_knowledge_matrix_js_syntax():
    """Verify visualizer/src/knowledge_matrix.js is valid ES6 checked by node."""
    assert os.path.exists(KNOWLEDGE_MATRIX_JS_PATH)
    res = subprocess.run(["node", "--check", KNOWLEDGE_MATRIX_JS_PATH], capture_output=True, text=True)
    assert res.returncode == 0, f"JavaScript syntax error in {KNOWLEDGE_MATRIX_JS_PATH}:\n{res.stderr}"


def test_knowledge_matrix_exports():
    """Verify knowledge_matrix.js exports KnowledgeMatrixManager and initKnowledgeMatrix."""
    node_script = """
    const matrixModule = require('./visualizer/src/knowledge_matrix.js');
    if (typeof matrixModule.initKnowledgeMatrix !== 'function') {
        throw new Error('initKnowledgeMatrix must be an exported function');
    }
    if (typeof matrixModule.KnowledgeMatrixManager !== 'function') {
        throw new Error('KnowledgeMatrixManager class must be exported');
    }
    console.log('EXPORTS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Matrix export verification failed:\n{res.stderr}\n{res.stdout}"
    assert "EXPORTS_OK" in res.stdout


# --- 3. Filtering & Sorting Algorithms Unit Tests ---

def test_matrix_multi_dimensional_filtering():
    """Verify multi-dimensional filtering across search text, domain, complexity, and category."""
    node_script = """
    const { KnowledgeMatrixManager } = require('./visualizer/src/knowledge_matrix.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    const manager = new KnowledgeMatrixManager(null, payload, null, null);

    // 1. Initial State: All 109 nodes
    if (manager.allNodes.length !== 109) {
        throw new Error('Expected 109 allNodes, got ' + manager.allNodes.length);
    }
    const initialFiltered = manager.filterEntities();
    if (initialFiltered.length !== 109) {
        throw new Error('Unfiltered count should be 109, got ' + initialFiltered.length);
    }

    // 2. Text Search Filtering
    // A) Search by node name/ID
    const searchRes1 = manager.filterEntities({ search: 'mt5_trader' });
    if (searchRes1.length === 0 || !searchRes1.some(n => n.id.includes('mt5'))) {
        throw new Error('Search by mt5_trader failed');
    }

    // B) Search by mathematical formulation / LaTeX
    const searchRes2 = manager.filterEntities({ search: 'Nelson-Siegel' });
    if (searchRes2.length === 0 || !searchRes2.some(n => n.id === 'quant_interest_rates')) {
        throw new Error('Search by LaTeX / math formulation failed');
    }

    // C) Search by code file path
    const searchRes3 = manager.filterEntities({ search: 'fred_backtest_data.py' });
    if (searchRes3.length === 0 || !searchRes3.some(n => n.id === 'd01_fred_backtest_data')) {
        throw new Error('Search by code file path failed');
    }

    // 3. Domain Filtering (Subsystems 1..12)
    const dom1Res = manager.filterEntities({ domain: 1 });
    if (dom1Res.length === 0 || !dom1Res.every(n => n.subsystem_id === 1)) {
        throw new Error('Domain 1 filter failed: ' + dom1Res.length);
    }

    const dom10Res = manager.filterEntities({ domain: 10 });
    if (dom10Res.length === 0 || !dom10Res.every(n => n.subsystem_id === 10)) {
        throw new Error('Domain 10 (Guardian) filter failed');
    }

    // 4. Category Filtering
    // A) Quant Theories (category === 'quant_theory')
    const quantTheories = manager.filterEntities({ category: 'quant_theory' });
    if (quantTheories.length !== 16) {
        throw new Error('Expected 16 quant theories, got ' + quantTheories.length);
    }

    // B) Protective Gates
    const gates = manager.filterEntities({ category: 'protective_gate' });
    if (gates.length !== 10) {
        throw new Error('Expected 10 protective gates, got ' + gates.length);
    }

    // C) Agent Roles
    const roles = manager.filterEntities({ category: 'agent_role' });
    if (roles.length !== 4) {
        throw new Error('Expected 4 agent roles, got ' + roles.length);
    }

    // D) Code Modules
    const modules = manager.filterEntities({ category: 'code_module' });
    if (modules.length !== 79) {
        throw new Error('Expected 79 code modules, got ' + modules.length);
    }

    // 5. Complexity Filtering
    // Foundational (L0_Gateway, L3_Persistence)
    const foundational = manager.filterEntities({ complexity: 'foundational' });
    if (foundational.length === 0) {
        throw new Error('Foundational complexity filter returned 0 nodes');
    }

    // Intermediate (L1_Orchestration)
    const intermediate = manager.filterEntities({ complexity: 'intermediate' });
    if (intermediate.length === 0) {
        throw new Error('Intermediate complexity filter returned 0 nodes');
    }

    // Advanced (L2_Strategy_Core or quant theories)
    const advanced = manager.filterEntities({ complexity: 'advanced' });
    if (advanced.length === 0) {
        throw new Error('Advanced complexity filter returned 0 nodes');
    }

    // Total of foundational + intermediate + advanced should equal 109
    if (foundational.length + intermediate.length + advanced.length !== 109) {
        throw new Error('Complexity partition sum (' +
            (foundational.length + intermediate.length + advanced.length) +
            ') != 109');
    }

    // 6. Combined Multi-Filter
    const combined = manager.filterEntities({
        domain: 1,
        category: 'quant_theory',
        complexity: 'advanced'
    });
    if (combined.length !== 2 || !combined.some(n => n.id === 'quant_interest_rates') || !combined.some(n => n.id === 'quant_time_series')) {
        throw new Error('Combined filter failed: expected [quant_time_series, quant_interest_rates], got ' + JSON.stringify(combined.map(n => n.id)));
    }

    console.log('FILTERING_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Filtering test failed:\n{res.stderr}\n{res.stdout}"
    assert "FILTERING_OK" in res.stdout


def test_matrix_sorting_algorithms():
    """Verify sorting by name, domain, tier level, and incoming callers/dependencies."""
    node_script = """
    const { KnowledgeMatrixManager } = require('./visualizer/src/knowledge_matrix.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    const manager = new KnowledgeMatrixManager(null, payload, null, null);

    // 1. Sort by Name (A-Z)
    const sortedByName = manager.filterEntities({ sort: 'name' });
    for (let i = 0; i < sortedByName.length - 1; i++) {
        const a = (sortedByName[i].name || sortedByName[i].id).toLowerCase();
        const b = (sortedByName[i+1].name || sortedByName[i+1].id).toLowerCase();
        if (a.localeCompare(b) > 0) {
            throw new Error(`Name sorting error: "${a}" should come before "${b}"`);
        }
    }

    // 2. Sort by Domain (Subsystem ID 1..12)
    const sortedByDomain = manager.filterEntities({ sort: 'domain' });
    for (let i = 0; i < sortedByDomain.length - 1; i++) {
        if (sortedByDomain[i].subsystem_id > sortedByDomain[i+1].subsystem_id) {
            throw new Error('Domain sorting out of order: ' + sortedByDomain[i].subsystem_id + ' > ' + sortedByDomain[i+1].subsystem_id);
        }
    }

    // 3. Sort by Callers (incoming dependencies count, descending)
    const sortedByCallers = manager.filterEntities({ sort: 'callers' });
    for (let i = 0; i < sortedByCallers.length - 1; i++) {
        const countA = (sortedByCallers[i].callers || []).length;
        const countB = (sortedByCallers[i+1].callers || []).length;
        if (countA < countB) {
            throw new Error('Callers sorting not descending: ' + countA + ' < ' + countB);
        }
    }

    console.log('SORTING_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Sorting test failed:\n{res.stderr}\n{res.stdout}"
    assert "SORTING_OK" in res.stdout


# --- 4. DOM Mounting, Card Grid Rendering & UI Lifecycle ---

def test_knowledge_matrix_dom_lifecycle():
    """Verify DOM element creation, card grid generation, summary badge, and open/close states."""
    node_script = """
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
            this.listeners = new Map();
        }

        get className() { return Array.from(this.classList._classes).join(' '); }
        set className(val) { this.classList._classes = new Set((val || '').split(/\\s+/).filter(Boolean)); }

        get innerHTML() { return this._innerHTML; }
        set innerHTML(val) {
            this._innerHTML = String(val || '');
            this.children = [];
            this._parseSimpleHTML(this._innerHTML);
        }

        get textContent() { return this._textContent || this._innerHTML.replace(/<[^>]*>/g, ''); }
        set textContent(val) { this._textContent = String(val || ''); this._innerHTML = String(val || ''); }

        setAttribute(k, v) { this.attributes.set(k, String(v)); }
        getAttribute(k) { return this.attributes.get(k) || null; }
        hasAttribute(k) { return this.attributes.has(k); }

        appendChild(ch) {
            ch.parentNode = this;
            this.children.push(ch);
            return ch;
        }

        addEventListener(ev, fn) {
            if (!this.listeners.has(ev)) this.listeners.set(ev, []);
            this.listeners.get(ev).push(fn);
        }

        dispatchEvent(event) {
            const evName = typeof event === 'string' ? event : event.type;
            const handlers = this.listeners.get(evName) || [];
            for (const h of handlers) {
                h.call(this, Object.assign({ target: this, currentTarget: this }, event));
            }
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

        _parseSimpleHTML(html) {
            this.children = [];
            const tagRegex = /<([a-zA-Z0-9]+)\s*([^>]*)>/g;
            let m;
            while ((m = tagRegex.exec(html)) !== null) {
                const tag = m[1];
                if (tag.startsWith('/')) continue;
                const attrs = m[2] || '';
                const el = new MockElement(tag);
                const classMatch = attrs.match(/class="([^"]*)"/i);
                if (classMatch) el.className = classMatch[1];
                const idMatch = attrs.match(/id="([^"]*)"/i);
                if (idMatch) el.id = idMatch[1];
                const dataActionMatch = attrs.match(/data-action="([^"]*)"/i);
                if (dataActionMatch) el.setAttribute('data-action', dataActionMatch[1]);
                const dataNodeIdMatch = attrs.match(/data-node-id="([^"]*)"/i);
                if (dataNodeIdMatch) el.setAttribute('data-node-id', dataNodeIdMatch[1]);
                
                const rest = html.slice(tagRegex.lastIndex);
                const nextTag = rest.indexOf('<');
                if (nextTag > 0) {
                    el.textContent = rest.slice(0, nextTag).trim();
                }
                this.children.push(el);
            }
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

    const { initKnowledgeMatrix } = require('./visualizer/src/knowledge_matrix.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    let focusedNodeId = null;
    const mockEngine = {
        focusNode: (id) => { focusedNodeId = id; },
        glideTo: (x, y, zoom) => {},
        resetCamera: () => {},
        nodeMap: new Map(payload.nodes.map(n => [n.id, n]))
    };

    let openedDrawerNodeId = null;
    const mockDrawer = {
        openDrawer: (id) => { openedDrawerNodeId = id; },
        closeDrawer: () => {}
    };

    const matrix = initKnowledgeMatrix(mockEngine, payload, mockDrawer, global.document.body);

    // 1. Verify Initial Mount
    const matrixView = global.document.querySelector('#knowledge-matrix-view');
    if (!matrixView) throw new Error('Missing #knowledge-matrix-view element in document');

    // 2. Open Matrix
    matrix.openMatrix();
    if (!matrix.isOpen) throw new Error('matrix.isOpen should be true after openMatrix()');
    if (!matrixView.classList.contains('matrix-open')) {
        throw new Error('matrixView should have class .matrix-open when open');
    }

    // 3. Verify Header and Summary Badge
    const title = matrixView.querySelector('.matrix-title');
    if (!title || !title.textContent.includes('KNOWLEDGE MATRIX')) {
        throw new Error('Missing or invalid .matrix-title');
    }
    const badge = matrixView.querySelector('.matrix-summary-badge');
    if (!badge || !badge.textContent.includes('109 ENTITIES')) {
        throw new Error('Missing or invalid .matrix-summary-badge: ' + (badge ? badge.textContent : 'none'));
    }

    // 4. Verify Cards Grid Rendering
    const grid = matrixView.querySelector('#matrix-card-grid');
    if (!grid) throw new Error('Missing #matrix-card-grid container');

    const cards = grid.querySelectorAll('.matrix-card');
    if (cards.length !== 109) {
        throw new Error('Expected 109 rendered cards, got ' + cards.length);
    }

    // Check card content: e.g. FredBacktestData
    const firstCard = cards[0];
    if (!firstCard.innerHTML.includes('FredBacktestData') && !firstCard.getAttribute('data-node-id')) {
        throw new Error('Card missing title or data-node-id');
    }

    // 5. Test Close Button
    const closeBtn = matrixView.querySelector('#matrix-close-btn') || matrixView.querySelector('.matrix-close-btn');
    if (!closeBtn) throw new Error('Missing matrix close button');
    closeBtn.dispatchEvent({ type: 'click' });
    if (matrix.isOpen) throw new Error('matrix.isOpen should be false after close button clicked');
    if (matrixView.classList.contains('matrix-open')) {
        throw new Error('matrixView should not have .matrix-open class when closed');
    }

    console.log('DOM_LIFECYCLE_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"DOM lifecycle test failed:\n{res.stderr}\n{res.stdout}"
    assert "DOM_LIFECYCLE_OK" in res.stdout


# --- 5. Transitions to Graph Canvas and Deep-Dive Drawer ---

def test_knowledge_matrix_transitions_and_actions():
    """Verify clicking Read Deep-Dive opens drawer and Focus in Graph closes matrix and focuses canvas node."""
    node_script = """
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
            this.listeners = new Map();
        }

        setAttribute(k, v) { this.attributes.set(k, String(v)); }
        getAttribute(k) { return this.attributes.get(k) || null; }
        hasAttribute(k) { return this.attributes.has(k); }

        get innerHTML() { return this._innerHTML; }
        set innerHTML(val) {
            this._innerHTML = String(val || '');
            this.children = [];
            // Parse simple buttons & divs
            const reg = /<(button|div)\\s+([^>]*)>([\\s\\S]*?)<\\/\\1>/gi;
            let m;
            while ((m = reg.exec(this._innerHTML)) !== null) {
                const tag = m[1];
                const attrs = m[2];
                const content = m[3];
                const el = new MockElement(tag);
                el.innerHTML = content;
                const classMatch = attrs.match(/class="([^"]*)"/i);
                if (classMatch) el.classList._classes = new Set(classMatch[1].split(/\\s+/).filter(Boolean));
                const idMatch = attrs.match(/id="([^"]*)"/i);
                if (idMatch) el.id = idMatch[1];
                const dataActionMatch = attrs.match(/data-action="([^"]*)"/i);
                if (dataActionMatch) el.setAttribute('data-action', dataActionMatch[1]);
                const dataNodeIdMatch = attrs.match(/data-node-id="([^"]*)"/i);
                if (dataNodeIdMatch) el.setAttribute('data-node-id', dataNodeIdMatch[1]);
                this.children.push(el);
            }
        }

        appendChild(ch) { ch.parentNode = this; this.children.push(ch); return ch; }
        addEventListener(ev, fn) {
            if (!this.listeners.has(ev)) this.listeners.set(ev, []);
            this.listeners.get(ev).push(fn);
        }
        dispatchEvent(event) {
            const evName = typeof event === 'string' ? event : event.type;
            const handlers = this.listeners.get(evName) || [];
            for (const h of handlers) h.call(this, Object.assign({ target: this, currentTarget: this }, event));
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

    const { initKnowledgeMatrix } = require('./visualizer/src/knowledge_matrix.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    let focusedNodeId = null;
    let selectedNodeId = null;
    const mockEngine = {
        focusNode: (id) => { focusedNodeId = id; },
        selectNode: (id) => { selectedNodeId = id; },
        nodeMap: new Map(payload.nodes.map(n => [n.id, n]))
    };

    let openedDrawerId = null;
    const mockDrawer = {
        openDrawer: (id) => { openedDrawerId = id; }
    };

    const matrix = initKnowledgeMatrix(mockEngine, payload, mockDrawer, global.document.body);
    matrix.openMatrix();

    // 1. Action: Read Deep Dive
    matrix.handleAction('deep-dive', 'quant_interest_rates');
    if (openedDrawerId !== 'quant_interest_rates') {
        throw new Error('Action deep-dive failed to open drawer for quant_interest_rates: ' + openedDrawerId);
    }

    // 2. Action: Focus in Graph
    matrix.handleAction('focus-graph', 'quant_interest_rates');
    if (matrix.isOpen) {
        throw new Error('Focus in Graph should close the matrix view');
    }
    if (focusedNodeId !== 'quant_interest_rates') {
        throw new Error('Action focus-graph failed to focus canvas node: ' + focusedNodeId);
    }

    console.log('TRANSITIONS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Transitions test failed:\n{res.stderr}\n{res.stdout}"
    assert "TRANSITIONS_OK" in res.stdout


# --- 6. Two-Way State Synchronization with Top Command Bar Perspective Switcher ---

def test_hud_perspective_matrix_synchronization():
    """Verify matrix perspective button in HUD opens knowledge matrix, and matrix exit reverts HUD perspective."""
    node_script = """
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
            this.listeners = new Map();
        }

        setAttribute(k, v) { this.attributes.set(k, String(v)); }
        getAttribute(k) { return this.attributes.get(k) || null; }
        hasAttribute(k) { return this.attributes.has(k); }

        get innerHTML() { return this._innerHTML; }
        set innerHTML(val) {
            this._innerHTML = String(val || '');
            this.children = [];
            const reg = /<(button|div)\\s+([^>]*)>([\\s\\S]*?)<\\/\\1>/gi;
            let m;
            while ((m = reg.exec(this._innerHTML)) !== null) {
                const tag = m[1];
                const attrs = m[2];
                const el = new MockElement(tag);
                el.innerHTML = m[3];
                const classMatch = attrs.match(/class="([^"]*)"/i);
                if (classMatch) el.classList._classes = new Set(classMatch[1].split(/\\s+/).filter(Boolean));
                const idMatch = attrs.match(/id="([^"]*)"/i);
                if (idMatch) el.id = idMatch[1];
                const dataPerspMatch = attrs.match(/data-perspective="([^"]*)"/i);
                if (dataPerspMatch) el.setAttribute('data-perspective', dataPerspMatch[1]);
                this.children.push(el);
            }
        }

        appendChild(ch) { ch.parentNode = this; this.children.push(ch); return ch; }
        addEventListener(ev, fn) {
            if (!this.listeners.has(ev)) this.listeners.set(ev, []);
            this.listeners.get(ev).push(fn);
        }
        dispatchEvent(event) {
            const evName = typeof event === 'string' ? event : event.type;
            const handlers = this.listeners.get(evName) || [];
            for (const h of handlers) h.call(this, Object.assign({ target: this, currentTarget: this }, event));
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
    const { initKnowledgeMatrix } = require('./visualizer/src/knowledge_matrix.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');

    const mockEngine = {
        focusNode: () => {},
        glideTo: () => {},
        resetCamera: () => {},
        nodeMap: new Map(payload.nodes.map(n => [n.id, n]))
    };

    const hud = initHUD(mockEngine, payload, global.document.body);
    const matrix = initKnowledgeMatrix(mockEngine, payload, null, global.document.body);

    // Wire two-way synchronization
    hud.setKnowledgeMatrix(matrix);
    matrix.setHUDControls(hud);

    // 1. Switching HUD perspective to 'matrix' opens knowledge matrix
    hud.setPerspective('matrix');
    if (!matrix.isOpen) {
        throw new Error('Setting HUD perspective to "matrix" did not open knowledge matrix');
    }

    // 2. Closing knowledge matrix reverts HUD perspective to 'code'
    matrix.closeMatrix();
    if (matrix.isOpen) {
        throw new Error('matrix should be closed after closeMatrix()');
    }
    if (hud.currentPerspective !== 'code') {
        throw new Error('Closing matrix did not revert HUD perspective to code, got: ' + hud.currentPerspective);
    }

    // 3. Clicking HUD matrix button directly
    const matrixBtn = global.document.querySelector('[data-perspective="matrix"]');
    if (!matrixBtn) throw new Error('Missing [data-perspective="matrix"] button');
    matrixBtn.dispatchEvent({ type: 'click' });
    if (!matrix.isOpen) {
        throw new Error('Clicking matrix perspective button did not open matrix');
    }

    console.log('HUD_MATRIX_SYNC_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"HUD-Matrix sync test failed:\n{res.stderr}\n{res.stdout}"
    assert "HUD_MATRIX_SYNC_OK" in res.stdout
