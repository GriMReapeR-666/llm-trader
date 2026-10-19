import json
import os
import subprocess
import pytest

STYLES_CSS_PATH = "visualizer/src/styles.css"
DEEP_DIVE_DRAWER_JS_PATH = "visualizer/src/deep_dive_drawer.js"
PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"
CANVAS_ENGINE_JS_PATH = "visualizer/src/canvas_engine.js"


# --- 1. File Existence & CSS Selectors ---

def test_drawer_files_exist():
    """Verify deep_dive_drawer.js and styles.css exist."""
    assert os.path.exists(STYLES_CSS_PATH), f"File {STYLES_CSS_PATH} must exist"
    assert os.path.exists(DEEP_DIVE_DRAWER_JS_PATH), f"File {DEEP_DIVE_DRAWER_JS_PATH} must exist"


def test_styles_css_drawer_classes():
    """Verify deep-dive drawer CSS rules exist with glassmorphic styling, transitions, and tags."""
    with open(STYLES_CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()

    required_selectors = [
        "#deep-dive-drawer",
        ".drawer-backdrop",
        ".drawer-header",
        ".drawer-close-btn",
        ".drawer-quick-actions",
        ".lens-toggle-container",
        ".lens-toggle-btn",
        ".eli5-container",
        ".quant-container",
        ".math-formula-box",
        ".variable-definitions-table",
        ".data-contract-section",
        ".contract-chip",
        ".dependency-explorer",
        ".caller-tag",
        ".dependency-tag",
        ".code-viewer-container",
        ".code-copy-btn",
        ".failure-mode-card",
        ".guardian-badge",
        ".citations-box",
    ]

    for sel in required_selectors:
        assert sel in css, f"Missing required CSS rule in styles.css: {sel}"

    # Verify smooth slide-in transform
    assert "transform" in css, "Drawer must use CSS transform for smooth slide transitions"
    assert "translateX" in css, "Drawer must use translateX for side slide-in"


# --- 2. JavaScript Syntax & Universal Export Verification ---

def test_deep_dive_drawer_js_syntax():
    """Verify visualizer/src/deep_dive_drawer.js is valid ES6 checked by node."""
    assert os.path.exists(DEEP_DIVE_DRAWER_JS_PATH)
    res = subprocess.run(["node", "--check", DEEP_DIVE_DRAWER_JS_PATH], capture_output=True, text=True)
    assert res.returncode == 0, f"JavaScript syntax error in {DEEP_DIVE_DRAWER_JS_PATH}:\n{res.stderr}"


def test_deep_dive_drawer_exports():
    """Verify deep_dive_drawer.js exports initDeepDiveDrawer, DeepDiveDrawer, and helper formatters."""
    node_script = """
    const drawerModule = require('./visualizer/src/deep_dive_drawer.js');
    if (typeof drawerModule.initDeepDiveDrawer !== 'function') {
        throw new Error('initDeepDiveDrawer must be an exported function');
    }
    if (typeof drawerModule.DeepDiveDrawer !== 'function') {
        throw new Error('DeepDiveDrawer class must be exported');
    }
    if (typeof drawerModule.formatMathToHtml !== 'function') {
        throw new Error('formatMathToHtml must be an exported function');
    }
    if (typeof drawerModule.highlightCode !== 'function') {
        throw new Error('highlightCode must be an exported function');
    }
    console.log('EXPORTS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Drawer export verification failed:\n{res.stderr}\n{res.stdout}"
    assert "EXPORTS_OK" in res.stdout


# --- 3. LaTeX Math Formatter & Code Highlighting Unit Tests ---

def test_math_formula_formatting():
    """Verify LaTeX math equations are parsed into readable HTML/SVG with Greek letters and math operators."""
    node_script = """
    const { formatMathToHtml } = require('./visualizer/src/deep_dive_drawer.js');
    const eq = '\\\\Delta y_t = y_t - y_{t-\\\\tau}, \\\\quad z_t = \\\\frac{\\\\Delta y_t - \\\\mu_\\\\Delta}{\\\\sigma_\\\\Delta}';
    const html = formatMathToHtml(eq);

    if (!html || typeof html !== 'string') {
        throw new Error('formatMathToHtml must return a non-empty string');
    }
    // Greek symbols replacement
    if (!html.includes('Δ') && !html.includes('&Delta;')) {
        throw new Error('LaTeX \\\\Delta should format to Greek Delta');
    }
    if (!html.includes('μ') && !html.includes('&mu;')) {
        throw new Error('LaTeX \\\\mu should format to Greek mu');
    }
    if (!html.includes('σ') && !html.includes('&sigma;')) {
        throw new Error('LaTeX \\\\sigma should format to Greek sigma');
    }
    if (!html.includes('τ') && !html.includes('&tau;')) {
        throw new Error('LaTeX \\\\tau should format to Greek tau');
    }
    // Fraction or division structure
    if (!html.includes('fraction') && !html.includes('frac-') && !html.includes('math-fraction') && !html.includes('divide')) {
        throw new Error('Fractions should be structured with math fraction class: ' + html);
    }
    console.log('MATH_FORMAT_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Math formatting test failed:\n{res.stderr}\n{res.stdout}"
    assert "MATH_FORMAT_OK" in res.stdout


def test_code_highlighter_and_line_numbers():
    """Verify code snippet highlighting formats Python keywords, strings, comments, and line numbers."""
    node_script = """
    const { highlightCode } = require('./visualizer/src/deep_dive_drawer.js');
    const snippet = `class FredBacktestData:
    def fetch_series(self, series_id: str) -> pd.Series:
        # Fetch from remote API
        data = self.client.get_series(series_id)
        return self._clean(data)`;

    const html = highlightCode(snippet, 'python');
    if (!html.includes('keyword') && !html.includes('token-keyword')) {
        throw new Error('Code highlighter should wrap keywords: ' + html);
    }
    if (!html.includes('def') || !html.includes('class')) {
        throw new Error('Code highlighter must preserve keywords def and class');
    }
    if (!html.includes('line-number') && !html.includes('line-num') && !html.includes('data-line')) {
        throw new Error('Code highlighter should output line numbering elements');
    }
    console.log('CODE_HIGHLIGHT_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Code highlighting test failed:\n{res.stderr}\n{res.stdout}"
    assert "CODE_HIGHLIGHT_OK" in res.stdout


# --- 4. Drawer DOM Mounting, Node Deep-Dive, and Lens Toggle ---

@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="needs node")
def test_drawer_mounting_and_node_rendering():
    """Verify deep-dive drawer mounts into DOM, populates node details, and renders all 8 schema attributes.

    Formerly xfail(strict) under "ROADMAP Phase 9 ... absent in the test DOM
    shim" — the shim now implements what the drawer needs, the test passes
    deterministically, and a stale strict-xfail fails the suite on every pass.
    """
    node_script = """
    // DOM Mock Environment
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
            // Parse top-level elements if useful
            this._parseBasicHTML(v);
        }

        _parseBasicHTML(html) {
            // Simplified parsing for querySelector in tests
            this.children = [];
            const tagRegex = /<([a-zA-Z0-9\\-]+)([^>]*)>([\\s\\S]*?)<\\/\\1>|<([a-zA-Z0-9\\-]+)([^>]*)\\/>/g;
            let match;
            while ((match = tagRegex.exec(html)) !== null) {
                const tagName = match[1] || match[4];
                const attrsStr = match[2] || match[5] || '';
                const inner = match[3] || '';
                const el = new MockElement(tagName);
                
                const idMatch = attrsStr.match(/id="([^"]+)"/);
                if (idMatch) el.id = idMatch[1];
                const classMatch = attrsStr.match(/class="([^"]+)"/);
                if (classMatch) el.className = classMatch[1];
                const dataIdMatch = attrsStr.match(/data-node-id="([^"]+)"/);
                if (dataIdMatch) el.setAttribute('data-node-id', dataIdMatch[1]);
                const dataLensMatch = attrsStr.match(/data-lens="([^"]+)"/);
                if (dataLensMatch) el.setAttribute('data-lens', dataLensMatch[1]);

                el.innerHTML = inner;
                this.appendChild(el);
            }
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

        querySelector(sel) {
            const all = this.querySelectorAll(sel);
            return all.length > 0 ? all[0] : null;
        }

        querySelectorAll(sel) {
            const results = [];
            const check = (node) => {
                let match = false;
                if (sel.startsWith('#') && node.id === sel.slice(1)) match = true;
                else if (sel.startsWith('.') && node.classList.contains(sel.slice(1))) match = true;
                else if (sel.startsWith('[data-lens="') && node.getAttribute('data-lens') === sel.slice(12, -2)) match = true;
                else if (sel.startsWith('[data-') && sel.endsWith(']')) {
                    const attr = sel.slice(1, -1).split('=');
                    const key = attr[0];
                    const val = attr[1] ? attr[1].replace(/['"]/g, '') : null;
                    if (val !== null) match = (node.getAttribute(key) === val);
                    else match = node.hasAttribute(key);
                }
                else if (sel.toLowerCase() === node.tagName.toLowerCase()) match = true;
                if (match) results.push(node);
                for (const c of node.children) check(c);
            };
            for (const c of this.children) check(c);
            return results;
        }

        addEventListener(event, fn) {
            if (!this.listeners.has(event)) this.listeners.set(event, []);
            this.listeners.get(event).push(fn);
        }

        dispatchEvent(event) {
            const fns = this.listeners.get(event.type) || [];
            for (const fn of fns) fn(event);
        }

        click() {
            this.dispatchEvent({ type: 'click', target: this });
        }
    }

    const documentMock = {
        body: new MockElement('BODY', 'body'),
        createElement: (tag) => new MockElement(tag),
        getElementById: function(id) {
            if (this.body.id === id) return this.body;
            return this.body.querySelector('#' + id);
        },
        querySelector: function(sel) {
            return this.body.querySelector(sel);
        },
        querySelectorAll: function(sel) {
            return this.body.querySelectorAll(sel);
        },
        addEventListener: () => {},
    };

    // Mock navigator clipboard
    let copiedText = '';
    const navigatorMock = {
        clipboard: {
            writeText: (txt) => {
                copiedText = txt;
                return Promise.resolve();
            }
        }
    };

    global.document = documentMock;
    global.window = {
        document: documentMock,
        navigator: navigatorMock,
        addEventListener: (ev, fn) => documentMock.addEventListener(ev, fn),
    };
    global.navigator = navigatorMock;

    // Load payload & modules
    const payload = require('./visualizer/data/knowledge_graph_payload.json');
    const { initDeepDiveDrawer } = require('./visualizer/src/deep_dive_drawer.js');

    let focusedNodeId = null;
    let switchedLayout = null;

    const mockCanvasEngine = {
        focusNode: (id) => { focusedNodeId = id; },
        switchLayout: (layout) => { switchedLayout = layout; },
        selectNode: (id) => {},
        nodes: payload.nodes,
        edges: payload.edges,
    };

    // Initialize Drawer
    const drawer = initDeepDiveDrawer(mockCanvasEngine, payload);
    const drawerEl = documentMock.getElementById('deep-dive-drawer');
    const backdropEl = documentMock.getElementById('deep-dive-backdrop');

    if (!drawerEl) throw new Error('#deep-dive-drawer element was not created');
    if (!backdropEl) throw new Error('#deep-dive-backdrop element was not created');

    // Open drawer for d01_fred_backtest_data
    drawer.openDrawer('d01_fred_backtest_data');

    if (!drawer.isOpen) throw new Error('drawer.isOpen should be true after openDrawer');
    if (!drawerEl.classList.contains('drawer-open')) {
        throw new Error('drawerEl should have .drawer-open class when open');
    }

    // Verify Title & Subsystem Badge
    const title = drawerEl.querySelector('.drawer-title') || drawerEl.querySelector('#drawer-node-title');
    if (!title || !drawerEl.innerHTML.includes('FredBacktestData')) {
        throw new Error('Drawer does not render node name FredBacktestData');
    }

    // Verify ELI5 Content
    if (!drawerEl.innerHTML.includes('Pulls historical and live macroeconomic data')) {
        throw new Error('Drawer does not render plain_english_summary');
    }

    // Verify Lens Toggle to Quant Rigor
    const quantToggleBtn = drawerEl.querySelector('[data-lens="quant"]');
    if (!quantToggleBtn) throw new Error('Missing [data-lens="quant"] toggle button');

    // Switch to Quant Rigor
    drawer.setLens('quant');
    if (drawer.activeLens !== 'quant') throw new Error('activeLens should be quant');
    if (!drawerEl.innerHTML.includes('math-formula-box') && !drawerEl.innerHTML.includes('LaTeX')) {
        throw new Error('Quant lens should render math formula container');
    }

    // Switch back to ELI5
    drawer.setLens('eli5');
    if (drawer.activeLens !== 'eli5') throw new Error('activeLens should be eli5');

    // Verify Cross-Link Jump from code module to theory
    // FredBacktestData has implements_theories: ['quant_interest_rates']
    const crossLinkBtn = drawerEl.querySelector('.cross-link-btn') || drawerEl.querySelector('[data-action="cross-link"]');
    if (!crossLinkBtn) throw new Error('Missing cross-link button for node with cross_links');

    drawer.jumpCrossLink('quant_interest_rates');
    if (drawer.currentNodeId !== 'quant_interest_rates') {
        throw new Error('Cross-link jump did not change currentNodeId to quant_interest_rates');
    }
    if (focusedNodeId !== 'quant_interest_rates') {
        throw new Error('Cross-link jump should call canvasEngine.focusNode');
    }

    // Verify Caller & Dependency Tag Click
    // quant_interest_rates callers: ['d01_fred_backtest_data']
    drawer.openDrawer('d01_fred_backtest_data');
    drawer.navigateToNode('quant_interest_rates');
    if (drawer.currentNodeId !== 'quant_interest_rates') {
        throw new Error('navigateToNode failed to open quant_interest_rates');
    }

    // Verify Quick Actions: Center Camera & Isolate Tree
    drawer.centerCamera();
    if (focusedNodeId !== 'quant_interest_rates') {
        throw new Error('centerCamera failed to focus current node');
    }

    drawer.isolateTree();
    if (switchedLayout !== 'tree') {
        throw new Error('isolateTree failed to switch canvas layout to tree');
    }

    // Verify Copy Code Path
    drawer.copyCodePath();
    if (!copiedText.includes('interest_rates.py')) {
        throw new Error('copyCodePath did not copy file_path: ' + copiedText);
    }

    // Close Drawer
    drawer.closeDrawer();
    if (drawer.isOpen) throw new Error('drawer should be closed after closeDrawer');
    if (drawerEl.classList.contains('drawer-open')) {
        throw new Error('drawerEl should not have .drawer-open class when closed');
    }

    console.log('DRAWER_MOUNT_AND_INTERACTIONS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Drawer lifecycle test failed:\n{res.stderr}\n{res.stdout}"
    assert "DRAWER_MOUNT_AND_INTERACTIONS_OK" in res.stdout


# --- 5. Data Contract, Circuit Breaker, & Citations Rendering ---

def test_drawer_contract_and_circuit_breakers_rendering():
    """Verify inputs/outputs data contracts, circuit breakers, and citations are rendered."""
    node_script = """
    const payload = require('./visualizer/data/knowledge_graph_payload.json');
    const { initDeepDiveDrawer } = require('./visualizer/src/deep_dive_drawer.js');

    const dom = {
        createElement: () => ({
            classList: { add: ()=>{}, remove: ()=>{}, contains: ()=>false },
            style: {},
            children: [],
            appendChild: function(c) { this.children.push(c); return c; },
            addEventListener: ()=>{},
            setAttribute: ()=>{},
            getAttribute: ()=>null
        }),
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        body: { appendChild: ()=>{} }
    };
    global.document = dom;
    global.window = { document: dom, addEventListener: ()=>{} };

    const drawer = initDeepDiveDrawer({ focusNode: ()=>{}, switchLayout: ()=>{} }, payload);
    const node = payload.nodes.find(n => n.id === 'd01_fred_backtest_data');
    const html = drawer.generateDrawerHTML(node);

    // Data contract inputs & outputs
    if (!html.includes('series_ids') || !html.includes('List[str]')) {
        throw new Error('Data contract inputs missing in drawer HTML');
    }
    if (!html.includes('macro_features_df') || !html.includes('pd.DataFrame')) {
        throw new Error('Data contract outputs missing in drawer HTML');
    }

    // Failure Modes & Guardian Response
    if (!html.includes('FRED API rate limit') || !html.includes('ALERT')) {
        throw new Error('Failure modes or Guardian badge missing in drawer HTML');
    }

    // Citations & Academic Grounding
    if (!html.includes('FRED-API-v2') || !html.includes('Federal Reserve Economic Data')) {
        throw new Error('Citations missing in drawer HTML');
    }

    console.log('CONTRACT_AND_BREAKERS_OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Contract and breakers test failed:\n{res.stderr}\n{res.stdout}"
    assert "CONTRACT_AND_BREAKERS_OK" in res.stdout
