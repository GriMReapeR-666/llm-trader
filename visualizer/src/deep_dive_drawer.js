/**
 * Cyber-Quant HyperGraph Studio - Universal Deep-Dive Slide-Over Drawer
 *
 * Provides a slide-over inspection drawer with ELI5 vs Institutional Quant Rigor toggle,
 * LaTeX mathematical equation rendering, parameter definitions, syntax-highlighted copyable
 * code viewer, input/output data contract chips, clickable emerald caller tags, rose dependency
 * tags, circuit breaker cards, and 1-click cross-linking between code files and quantitative theories.
 *
 * Zero external runtime dependencies.
 */

(function (global) {
  'use strict';

  // Subsystem 1..12 Palette and Meta
  const SUBSYSTEM_INFO = {
    1: { name: 'Market Data & Macro Ingestion', color: '#38bdf8' },
    2: { name: 'Macro & Regime Classification', color: '#818cf8' },
    3: { name: 'Alpha Mining & Factor Discovery', color: '#34d399' },
    4: { name: 'Multi-Agent LLM Deliberation', color: '#c084fc' },
    5: { name: 'Causal Inference & DAG Discovery', color: '#f472b6' },
    6: { name: 'Overfit & Deflated Sharpe Gating', color: '#fb923c' },
    7: { name: 'Quantitative Risk Allocation', color: '#f87171' },
    8: { name: 'Continual Learning & Meta-loop', color: '#a78bfa' },
    9: { name: 'Core MT5 Execution Gateway', color: '#4ade80' },
    10: { name: 'Guardian Sentinel & Circuit Breakers', color: '#facc15' },
    11: { name: 'Memory Persistence & State Store', color: '#2dd4bf' },
    12: { name: 'Mission Control & Observability', color: '#60a5fa' },
  };

  const DEFAULT_COLOR = '#94a3b8';

  /**
   * Parse LaTeX math equation into formatted, semantic HTML.
   * Renders Greek letters, fractions, super/subscripts, operators, and matrices.
   * @param {string} latex
   * @returns {string} HTML string
   */
  function formatMathToHtml(latex) {
    if (!latex || typeof latex !== 'string') return '';

    let out = latex.trim();

    // 1. Fractions: \frac{num}{den}
    const fracRegex = /\\frac\{([^{}]+)\}\{([^{}]+)\}/g;
    let iterations = 0;
    while (fracRegex.test(out) && iterations < 10) {
      out = out.replace(fracRegex, (m, num, den) => {
        return `<span class="math-fraction"><span class="fraction-num">${num}</span><span class="fraction-bar"></span><span class="fraction-den">${den}</span></span>`;
      });
      iterations++;
    }

    // 2. Greek Symbols
    const greekMap = {
      '\\\\Delta': 'Δ',
      '\\\\delta': 'δ',
      '\\\\alpha': 'α',
      '\\\\beta': 'β',
      '\\\\gamma': 'γ',
      '\\\\Gamma': 'Γ',
      '\\\\theta': 'θ',
      '\\\\Theta': 'Θ',
      '\\\\lambda': 'λ',
      '\\\\Lambda': 'Λ',
      '\\\\mu': 'μ',
      '\\\\sigma': 'σ',
      '\\\\Sigma': 'Σ',
      '\\\\tau': 'τ',
      '\\\\pi': 'π',
      '\\\\Pi': 'Π',
      '\\\\rho': 'ρ',
      '\\\\omega': 'ω',
      '\\\\Omega': 'Ω',
      '\\\\phi': 'φ',
      '\\\\Phi': 'Φ',
      '\\\\epsilon': 'ε',
      '\\\\varepsilon': 'ε',
      '\\\\eta': 'η',
      '\\\\chi': 'χ',
      '\\\\psi': 'ψ',
      '\\\\Psi': 'Ψ',
      '\\\\zeta': 'ζ',
    };

    for (const [pattern, replacement] of Object.entries(greekMap)) {
      out = out.replace(new RegExp(pattern, 'g'), replacement);
    }

    // 3. Mathematical Operators & Symbols
    const symbolMap = {
      '\\\\sum': '<span class="math-op math-sum">∑</span>',
      '\\\\prod': '<span class="math-op math-prod">∏</span>',
      '\\\\int': '<span class="math-op math-int">∫</span>',
      '\\\\infty': '∞',
      '\\\\times': '×',
      '\\\\cdot': '·',
      '\\\\dots': '…',
      '\\\\cdots': '⋯',
      '\\\\partial': '∂',
      '\\\\nabla': '∇',
      '\\\\in': '∈',
      '\\\\approx': '≈',
      '\\\\propto': '∝',
      '\\\\neq': '≠',
      '\\\\le': '≤',
      '\\\\ge': '≥',
      '\\\\pm': '±',
      '\\\\mid': '|',
      '\\\\quad': '&emsp;',
      '\\\\qquad': '&emsp;&emsp;',
      '\\\\;': '&ensp;',
      '\\\\,': '&hairsp;',
      '\\\\mathbb\\{I\\}': '<span class="math-bb">𝟙</span>',
      '\\\\mathbb\\{R\\}': '<span class="math-bb">ℝ</span>',
      '\\\\mathbb\\{E\\}': '<span class="math-bb">𝔼</span>',
      '\\\\mathcal\\{L\\}': '<span class="math-cal">ℒ</span>',
      '\\\\mathcal\\{D\\}': '<span class="math-cal">𝒟</span>',
      '\\\\mathcal\\{N\\}': '<span class="math-cal">𝒩</span>',
    };

    for (const [pattern, replacement] of Object.entries(symbolMap)) {
      out = out.replace(new RegExp(pattern, 'g'), replacement);
    }

    // 4. \text{...} and \mathbf{...}
    out = out.replace(/\\text\{([^{}]+)\}/g, '<span class="math-text">$1</span>');
    out = out.replace(/\\mathbf\{([^{}]+)\}/g, '<strong class="math-bold">$1</strong>');
    out = out.replace(/\\mathcal\{([^{}]+)\}/g, '<span class="math-cal">$1</span>');

    // 5. Brackets
    out = out.replace(/\\left\(/g, '<span class="math-paren">(</span>');
    out = out.replace(/\\right\)/g, '<span class="math-paren">)</span>');
    out = out.replace(/\\left\[/g, '<span class="math-bracket">[</span>');
    out = out.replace(/\\right\]/g, '<span class="math-bracket">]</span>');
    out = out.replace(/\\left\\{/g, '<span class="math-brace">{</span>');
    out = out.replace(/\\right\\}/g, '<span class="math-brace">}</span>');

    // 6. Subscripts: _{...} and _x
    out = out.replace(/_\{([^{}]+)\}/g, '<sub>$1</sub>');
    out = out.replace(/_([a-zA-Z0-9Δδθλμστβα])/g, '<sub>$1</sub>');

    // 7. Superscripts: ^{...} and ^x
    out = out.replace(/\^\{([^{}]+)\}/g, '<sup>$1</sup>');
    out = out.replace(/\^([a-zA-Z0-9Δδθλμστβα\-+])/g, '<sup>$1</sup>');

    // Clean remaining backslashes for standard identifiers
    out = out.replace(/\\([a-zA-Z]+)/g, '$1');

    return `<div class="math-formula-box"><div class="math-equation-row">${out}</div></div>`;
  }

  /**
   * Minimal lightweight syntax highlighter for Python / code snippets with line numbering.
   * @param {string} code
   * @param {string} [lang='python']
   * @returns {string} HTML string
   */
  function highlightCode(code, lang = 'python') {
    if (!code || typeof code !== 'string') return '';

    const lines = code.split('\n');
    const highlightedLines = lines.map((line, idx) => {
      const lineNum = idx + 1;

      // Escape basic HTML
      let escaped = line
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

      // Comments: # ...
      let commentPart = '';
      const commentIdx = escaped.indexOf('#');
      if (commentIdx !== -1) {
        commentPart = `<span class="token-comment">${escaped.slice(commentIdx)}</span>`;
        escaped = escaped.slice(0, commentIdx);
      }

      // Strings: "..." or '...'
      escaped = escaped.replace(/(["'])(?:(?=(\\?))\2[\s\S])*?\1/g, '<span class="token-string">$&</span>');

      // Keywords
      const keywords = [
        'def', 'class', 'return', 'import', 'from', 'if', 'else', 'elif',
        'for', 'while', 'in', 'try', 'except', 'finally', 'with', 'as',
        'None', 'True', 'False', 'self', 'lambda', 'async', 'await', 'raise', 'yield'
      ];
      const kwRegex = new RegExp(`\\b(${keywords.join('|')})\\b`, 'g');
      escaped = escaped.replace(kwRegex, '<span class="token-keyword keyword">$1</span>');

      // Function definition names: def fname(
      escaped = escaped.replace(/(<span class="token-keyword keyword">def<\/span>\s+)([a-zA-Z0-9_]+)/g, '$1<span class="token-function">$2</span>');
      // Class names: class CName
      escaped = escaped.replace(/(<span class="token-keyword keyword">class<\/span>\s+)([a-zA-Z0-9_]+)/g, '$1<span class="token-class">$2</span>');

      const fullLine = escaped + commentPart;
      return `<div class="code-line" data-line="${lineNum}"><span class="line-number">${lineNum}</span><span class="line-content">${fullLine || ' '}</span></div>`;
    });

    return `<div class="code-block-highlighted">${highlightedLines.join('')}</div>`;
  }

  /**
   * DeepDiveDrawer Manager Class
   */
  class DeepDiveDrawer {
    /**
     * @param {Object} canvasEngine
     * @param {Object} graphData
     */
    constructor(canvasEngine, graphData = {}) {
      this.engine = canvasEngine;
      this.data = graphData;
      this.nodes = graphData.nodes || [];
      this.edges = graphData.edges || [];
      this.nodeMap = new Map();
      for (const node of this.nodes) {
        this.nodeMap.set(node.id, node);
      }

      this.isOpen = false;
      this.currentNodeId = null;
      this.activeLens = 'eli5'; // 'eli5' | 'quant'

      this.drawerEl = null;
      this.backdropEl = null;

      this._boundOnKeyDown = this._onKeyDown.bind(this);

      this._initDOM();
      this._wireEngine();
    }

    /**
     * Initialize DOM elements (#deep-dive-drawer, #deep-dive-backdrop) and attach listeners.
     */
    _initDOM() {
      if (typeof document === 'undefined') return;

      // Backdrop overlay
      let backdrop = document.getElementById('deep-dive-backdrop');
      if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.id = 'deep-dive-backdrop';
        backdrop.className = 'drawer-backdrop';
        backdrop.setAttribute('aria-hidden', 'true');
        backdrop.addEventListener('click', () => this.closeDrawer());
        document.body.appendChild(backdrop);
      }
      this.backdropEl = backdrop;

      // Slide-over Drawer
      let drawer = document.getElementById('deep-dive-drawer');
      if (!drawer) {
        drawer = document.createElement('aside');
        drawer.id = 'deep-dive-drawer';
        drawer.className = 'deep-dive-drawer';
        drawer.setAttribute('aria-hidden', 'true');
        drawer.setAttribute('role', 'dialog');
        drawer.setAttribute('aria-labelledby', 'drawer-node-title');
        document.body.appendChild(drawer);
      }
      this.drawerEl = drawer;

      // Keydown listener for Escape key
      if (typeof window !== 'undefined') {
        window.addEventListener('keydown', this._boundOnKeyDown);
      }
    }

    /**
     * Wire up canvas engine event hooks so canvas clicks seamlessly open this drawer.
     */
    _wireEngine() {
      if (!this.engine) return;

      const self = this;
      const prevSelect = this.engine.onNodeSelect;
      this.engine.onNodeSelect = function (node) {
        if (typeof prevSelect === 'function') {
          prevSelect.apply(this, arguments);
        }
        if (node && node.id) {
          self.openDrawer(node.id);
        } else if (!node) {
          // If canvas empty space is clicked, we can keep or close drawer
        }
      };

      const prevSelected = this.engine.onNodeSelected;
      this.engine.onNodeSelected = function (node) {
        if (typeof prevSelected === 'function') {
          prevSelected.apply(this, arguments);
        }
        if (node && node.id) {
          self.openDrawer(node.id);
        }
      };

      // Also wire global hudControls if available
      if (typeof window !== 'undefined' && window.hudControls) {
        const prevHudSelect = window.hudControls.onNodeSelected;
        window.hudControls.onNodeSelected = function (node) {
          if (typeof prevHudSelect === 'function') {
            prevHudSelect.apply(this, arguments);
          }
          if (node && node.id) {
            self.openDrawer(node.id);
          }
        };
      }
    }

    _onKeyDown(e) {
      if (e.key === 'Escape' && this.isOpen) {
        this.closeDrawer();
      }
    }

    /**
     * Open deep-dive drawer for specified node ID.
     * @param {string} nodeId
     */
    openDrawer(nodeId) {
      const node = this.nodeMap.get(nodeId);
      if (!node) {
        console.warn(`[DeepDiveDrawer] Node not found: ${nodeId}`);
        return;
      }

      this.currentNodeId = nodeId;
      this.isOpen = true;

      if (this.drawerEl) {
        this.drawerEl.innerHTML = this.generateDrawerHTML(node);
        this.drawerEl.classList.add('drawer-open');
        this.drawerEl.setAttribute('aria-hidden', 'false');
        this._bindDrawerEvents();
      }

      if (this.backdropEl) {
        this.backdropEl.classList.add('visible');
        this.backdropEl.setAttribute('aria-hidden', 'false');
      }

      // Sync active lens UI
      this._updateLensVisibility();
    }

    /**
     * Close the deep dive drawer.
     */
    closeDrawer() {
      this.isOpen = false;
      if (this.drawerEl) {
        this.drawerEl.classList.remove('drawer-open');
        this.drawerEl.setAttribute('aria-hidden', 'true');
      }
      if (this.backdropEl) {
        this.backdropEl.classList.remove('visible');
        this.backdropEl.setAttribute('aria-hidden', 'true');
      }
    }

    /**
     * Set active lens toggle ('eli5' or 'quant').
     * @param {'eli5'|'quant'} lensName
     */
    setLens(lensName) {
      if (lensName !== 'eli5' && lensName !== 'quant') return;
      this.activeLens = lensName;
      this._updateLensVisibility();
    }

    _updateLensVisibility() {
      if (!this.drawerEl) return;

      // Update toggle buttons
      const buttons = this.drawerEl.querySelectorAll('.lens-toggle-btn');
      for (const btn of buttons) {
        const lens = btn.getAttribute('data-lens');
        if (lens === this.activeLens) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      }

      // Update container views
      const eli5Box = this.drawerEl.querySelector('.eli5-container');
      const quantBox = this.drawerEl.querySelector('.quant-container');

      if (eli5Box) {
        eli5Box.style.display = (this.activeLens === 'eli5') ? 'block' : 'none';
      }
      if (quantBox) {
        quantBox.style.display = (this.activeLens === 'quant') ? 'block' : 'none';
      }
    }

    /**
     * Navigate drawer directly to another node and center canvas camera.
     * @param {string} nodeId
     */
    navigateToNode(nodeId) {
      if (!nodeId) return;
      this.openDrawer(nodeId);
      this.centerCamera();
    }

    /**
     * Smoothly focus canvas camera on current node.
     */
    centerCamera() {
      if (!this.currentNodeId || !this.engine) return;
      if (typeof this.engine.focusNode === 'function') {
        this.engine.focusNode(this.currentNodeId, 1.4);
      }
    }

    /**
     * Switch canvas layout to isolate tree hierarchy.
     */
    isolateTree() {
      if (!this.engine) return;
      if (typeof this.engine.switchLayout === 'function') {
        this.engine.switchLayout('tree');
      }
      if (this.currentNodeId && typeof this.engine.focusNode === 'function') {
        this.engine.focusNode(this.currentNodeId, 1.2);
      }
    }

    /**
     * Copy node's code file path to clipboard.
     */
    copyCodePath() {
      const node = this.nodeMap.get(this.currentNodeId);
      if (!node) return;
      const path = (node.code_realization && node.code_realization.file_path) || node.id;
      this._writeToClipboard(path, '.copy-path-btn', 'Copied Path!');
    }

    /**
     * Copy full code snippet to clipboard.
     */
    copyCodeSnippet() {
      const node = this.nodeMap.get(this.currentNodeId);
      if (!node || !node.code_realization) return;
      const code = node.code_realization.code_snippet || '';
      this._writeToClipboard(code, '.code-copy-btn', 'Copied!');
    }

    _writeToClipboard(text, buttonSelector, successLabel) {
      if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          this._showButtonSuccess(buttonSelector, successLabel);
        }).catch(() => {
          this._fallbackCopy(text);
          this._showButtonSuccess(buttonSelector, successLabel);
        });
      } else {
        this._fallbackCopy(text);
        this._showButtonSuccess(buttonSelector, successLabel);
      }
    }

    _fallbackCopy(text) {
      if (typeof document === 'undefined') return;
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
      } catch (err) {
        console.warn('Clipboard write fallback failed', err);
      }
      document.body.removeChild(ta);
    }

    _showButtonSuccess(selector, label) {
      if (!this.drawerEl) return;
      const btn = this.drawerEl.querySelector(selector);
      if (!btn) return;
      const origText = btn.innerHTML;
      btn.innerHTML = `<span class="copy-success-icon">✓</span> ${label}`;
      btn.classList.add('copy-success');
      setTimeout(() => {
        btn.innerHTML = origText;
        btn.classList.remove('copy-success');
      }, 1600);
    }

    /**
     * Jump along cross-link between code module and quant theory.
     * @param {string} [explicitTargetId]
     */
    jumpCrossLink(explicitTargetId = null) {
      if (explicitTargetId) {
        this.navigateToNode(explicitTargetId);
        return;
      }

      const node = this.nodeMap.get(this.currentNodeId);
      if (!node || !node.cross_links) return;

      let targetId = null;
      if (node.cross_links.implements_theories && node.cross_links.implements_theories.length > 0) {
        targetId = node.cross_links.implements_theories[0];
      } else if (node.cross_links.implemented_by_code && node.cross_links.implemented_by_code.length > 0) {
        targetId = node.cross_links.implemented_by_code[0];
      }

      if (targetId) {
        this.navigateToNode(targetId);
      }
    }

    /**
     * Bind click events inside rendered drawer DOM.
     */
    _bindDrawerEvents() {
      if (!this.drawerEl) return;

      // Close button
      const closeBtn = this.drawerEl.querySelector('.drawer-close-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => this.closeDrawer());
      }

      // Quick Action: Center Camera
      const centerBtn = this.drawerEl.querySelector('[data-action="center-camera"]');
      if (centerBtn) {
        centerBtn.addEventListener('click', () => this.centerCamera());
      }

      // Quick Action: Isolate Tree
      const treeBtn = this.drawerEl.querySelector('[data-action="isolate-tree"]');
      if (treeBtn) {
        treeBtn.addEventListener('click', () => this.isolateTree());
      }

      // Quick Action: Copy Code Path
      const copyPathBtn = this.drawerEl.querySelector('[data-action="copy-path"]');
      if (copyPathBtn) {
        copyPathBtn.addEventListener('click', () => this.copyCodePath());
      }

      // Quick Action: Cross Link Jump
      const crossLinkBtn = this.drawerEl.querySelector('[data-action="cross-link"]');
      if (crossLinkBtn) {
        crossLinkBtn.addEventListener('click', () => {
          const targetId = crossLinkBtn.getAttribute('data-target-id');
          this.jumpCrossLink(targetId);
        });
      }

      // Lens Toggle buttons
      const lensButtons = this.drawerEl.querySelectorAll('.lens-toggle-btn');
      for (const btn of lensButtons) {
        btn.addEventListener('click', () => {
          const lens = btn.getAttribute('data-lens');
          this.setLens(lens);
        });
      }

      // Code copy button
      const codeCopyBtn = this.drawerEl.querySelector('.code-copy-btn');
      if (codeCopyBtn) {
        codeCopyBtn.addEventListener('click', () => this.copyCodeSnippet());
      }

      // Caller Tags Click
      const callerTags = this.drawerEl.querySelectorAll('.caller-tag');
      for (const tag of callerTags) {
        tag.addEventListener('click', () => {
          const targetId = tag.getAttribute('data-node-id');
          this.navigateToNode(targetId);
        });
      }

      // Dependency Tags Click
      const depTags = this.drawerEl.querySelectorAll('.dependency-tag');
      for (const tag of depTags) {
        tag.addEventListener('click', () => {
          const targetId = tag.getAttribute('data-node-id');
          this.navigateToNode(targetId);
        });
      }

      // Contract chip source/target navigation
      const contractChips = this.drawerEl.querySelectorAll('.contract-nav-chip');
      for (const chip of contractChips) {
        chip.addEventListener('click', () => {
          const targetId = chip.getAttribute('data-node-id');
          if (this.nodeMap.has(targetId)) {
            this.navigateToNode(targetId);
          }
        });
      }
    }

    /**
     * Generate rich HTML string for a given node.
     * @param {Object} node
     * @returns {string} HTML markup
     */
    generateDrawerHTML(node) {
      if (!node) return '';

      const subInfo = SUBSYSTEM_INFO[node.subsystem_id] || { name: `Subsystem ${node.subsystem_id}`, color: DEFAULT_COLOR };
      const subColor = subInfo.color;

      // Tier format
      const tierFormatted = (node.tier_level || 'Core').replace(/_/g, ' ');
      const categoryFormatted = (node.category || 'module').replace(/_/g, ' ');

      // Determine cross link target
      let crossLinkTargetId = null;
      let crossLinkLabel = 'Cross-Link Jump';
      let crossLinkTitle = 'Jump to linked concept';
      if (node.cross_links) {
        if (node.cross_links.implements_theories && node.cross_links.implements_theories.length > 0) {
          crossLinkTargetId = node.cross_links.implements_theories[0];
          const targetNode = this.nodeMap.get(crossLinkTargetId);
          crossLinkLabel = `Grounding: ${targetNode ? targetNode.name : crossLinkTargetId}`;
          crossLinkTitle = `Jump to Theoretical Grounding: ${crossLinkTargetId}`;
        } else if (node.cross_links.implemented_by_code && node.cross_links.implemented_by_code.length > 0) {
          crossLinkTargetId = node.cross_links.implemented_by_code[0];
          const targetNode = this.nodeMap.get(crossLinkTargetId);
          crossLinkLabel = `Implementation: ${targetNode ? targetNode.name : crossLinkTargetId}`;
          crossLinkTitle = `Jump to Code Implementation: ${crossLinkTargetId}`;
        }
      }

      // 1. Math formulation HTML
      let mathHtml = '';
      let variablesHtml = '';
      let proofHtml = '';
      if (node.mathematical_formulation) {
        const mf = node.mathematical_formulation;
        if (mf.latex_equation) {
          mathHtml = formatMathToHtml(mf.latex_equation);
        }
        if (mf.variables && typeof mf.variables === 'object') {
          const rows = Object.entries(mf.variables).map(([sym, desc]) => {
            const symHtml = formatMathToHtml(sym).replace(/class="math-formula-box"/g, 'class="var-sym-box"');
            return `<tr><td class="var-symbol">${symHtml || sym}</td><td class="var-meaning">${desc}</td></tr>`;
          }).join('');
          variablesHtml = `
            <div class="variable-definitions-container">
              <h4 class="drawer-subsection-title">Variable & Parameter Mapping</h4>
              <table class="variable-definitions-table">
                <thead>
                  <tr><th>Symbol</th><th>Institutional Financial / Algorithmic Meaning</th></tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>`;
        }
        if (mf.algorithmic_proof) {
          proofHtml = `
            <div class="proof-breakdown">
              <h4 class="drawer-subsection-title">Algorithmic Proof & Objective Function</h4>
              <p class="proof-text">${mf.algorithmic_proof}</p>
            </div>`;
        }
      }

      // 2. ELI5 Derived Sections
      const eli5Summary = node.plain_english_summary || 'No Plain English summary provided.';
      
      // Intuitive Analogy
      let intuitiveAnalogy = '';
      if (node.category === 'quant_theory') {
        intuitiveAnalogy = `Think of this like an airplane's flight control equation: instead of guessing where the market wind is blowing, it calculates the exact aerodynamic lift and turbulence so the trading craft never stalls out.`;
      } else if (node.category === 'protective_gate') {
        intuitiveAnalogy = `Think of this like an ultra-sensitive emergency electrical fuse in a high-voltage power grid: the millisecond unexpected voltage spikes occur, it cuts the circuit before precious capital is vaporized.`;
      } else if (node.category === 'agent_role') {
        intuitiveAnalogy = `Think of this like a specialized senior analyst sitting at an investment committee table with veto power, cross-examining every bullish pitch before a dime of money is risked.`;
      } else {
        intuitiveAnalogy = `Think of this like a high-speed telemetry sensor on a Formula-1 car: constantly measuring oil pressure and tire grip to ensure the vehicle drives at maximum speed without blowing the engine.`;
      }

      // Capital Preservation & Protection
      let capitalProtection = '';
      if (node.failure_modes && node.failure_modes.defense_mechanism) {
        capitalProtection = `Protects capital by continuously guarding against ${node.failure_modes.edge_case.toLowerCase()}. Automated response: ${node.failure_modes.defense_mechanism}.`;
      } else {
        capitalProtection = `Prevents unhedged exposure, catastrophic drawdown during black-swan shocks, and model overfitting on historical noise.`;
      }

      // Trading Practical Example
      let tradingExample = `In live trading, when incoming tick feeds or economic surprises flash across the tape, this component executes in < 5ms, validating statistical significance before allowing the MT5 gateway to route order tickets.`;

      // 3. Data Contracts
      let inputsHtml = '<span class="empty-contract-text">None (Source root)</span>';
      if (node.data_contract && node.data_contract.inputs && node.data_contract.inputs.length > 0) {
        inputsHtml = node.data_contract.inputs.map(inp => {
          const isKnownNode = this.nodeMap.has(inp.source_node);
          const navClass = isKnownNode ? 'contract-nav-chip' : '';
          return `
            <div class="contract-chip input-chip ${navClass}" data-node-id="${inp.source_node}" title="Source: ${inp.source_node}">
              <span class="chip-label">IN</span>
              <span class="chip-name">${inp.name}</span>
              <span class="chip-type">${inp.type}</span>
              <span class="chip-origin">← ${inp.source_node}</span>
            </div>`;
        }).join('');
      }

      let outputsHtml = '<span class="empty-contract-text">None (Terminal sink)</span>';
      if (node.data_contract && node.data_contract.outputs && node.data_contract.outputs.length > 0) {
        outputsHtml = node.data_contract.outputs.map(out => {
          const isKnownNode = this.nodeMap.has(out.target_node);
          const navClass = isKnownNode ? 'contract-nav-chip' : '';
          return `
            <div class="contract-chip output-chip ${navClass}" data-node-id="${out.target_node}" title="Target: ${out.target_node}">
              <span class="chip-label">OUT</span>
              <span class="chip-name">${out.name}</span>
              <span class="chip-type">${out.type}</span>
              <span class="chip-dest">→ ${out.target_node}</span>
            </div>`;
        }).join('');
      }

      // 4. Callers & Dependencies
      let callersHtml = '<span class="empty-relation-text">No incoming callers</span>';
      if (node.callers && node.callers.length > 0) {
        callersHtml = node.callers.map(cId => {
          const cNode = this.nodeMap.get(cId);
          const label = cNode ? cNode.name : cId;
          return `<button class="caller-tag" data-node-id="${cId}" title="Inspect caller: ${cId}">← ${label}</button>`;
        }).join('');
      }

      let depsHtml = '<span class="empty-relation-text">No downstream dependencies</span>';
      if (node.dependencies && node.dependencies.length > 0) {
        depsHtml = node.dependencies.map(dId => {
          const dNode = this.nodeMap.get(dId);
          const label = dNode ? dNode.name : dId;
          return `<button class="dependency-tag" data-node-id="${dId}" title="Inspect dependency: ${dId}">→ ${label}</button>`;
        }).join('');
      }

      // 5. Code Realization
      let codeViewerHtml = '';
      if (node.code_realization) {
        const cr = node.code_realization;
        const filePath = cr.file_path || 'unknown/path.py';
        const signature = cr.signature || '';
        const snippet = cr.code_snippet || '';
        const highlighted = highlightCode(snippet, 'python');

        codeViewerHtml = `
          <div class="code-viewer-container">
            <div class="code-viewer-toolbar">
              <div class="code-meta-info">
                <span class="code-file-icon">📄</span>
                <span class="code-file-path" title="${filePath}">${filePath}</span>
                <span class="code-loc-badge">${node.loc || 0} LOC</span>
              </div>
              <button class="code-copy-btn" title="Copy code snippet to clipboard">
                <span class="copy-icon">📋</span> Copy Code
              </button>
            </div>
            ${signature ? `<div class="code-signature-bar"><code>${signature}</code></div>` : ''}
            <div class="code-scroll-pane">${highlighted}</div>
          </div>`;
      }

      // 6. Failure Modes & Circuit Breaker
      let failureHtml = '';
      if (node.failure_modes) {
        const fm = node.failure_modes;
        const resp = (fm.guardian_response || 'ALERT').toUpperCase();
        let badgeColorClass = 'guardian-alert';
        if (resp === 'SHUTDOWN') badgeColorClass = 'guardian-shutdown';
        if (resp === 'THROTTLE') badgeColorClass = 'guardian-throttle';

        failureHtml = `
          <div class="failure-mode-card">
            <div class="failure-header">
              <div class="failure-title-group">
                <span class="failure-icon">⚡</span>
                <h4 class="failure-title">Operational Failure Modes & Circuit Breakers</h4>
              </div>
              <span class="guardian-badge ${badgeColorClass}" title="Guardian Sentinel Action">${resp}</span>
            </div>
            <div class="failure-body">
              <div class="failure-field">
                <span class="failure-field-label">Black Swan / Stress Scenario:</span>
                <span class="failure-field-value">${fm.edge_case || 'None documented'}</span>
              </div>
              <div class="failure-field">
                <span class="failure-field-label">Autonomous Bot Defense:</span>
                <span class="failure-field-value">${fm.defense_mechanism || 'Standard error propagation'}</span>
              </div>
            </div>
          </div>`;
      }

      // 7. Citations & Academic Grounding
      let citationsHtml = '';
      if (node.citations) {
        const cit = node.citations;
        citationsHtml = `
          <div class="citations-box">
            <div class="citations-header">
              <span class="citation-icon">📚</span>
              <span class="citation-title">Academic & Institutional Grounding</span>
              <span class="citation-category-pill">${cit.category || 'Quantitative Finance'}</span>
            </div>
            <div class="citation-content">
              <div class="citation-source-title">${cit.source_title || 'Institutional Source'}</div>
              <div class="citation-doi-row">
                <span class="doi-label">Citation Key / DOI / Video ID:</span>
                <code class="doi-code">${cit.video_id_or_doi || 'N/A'}</code>
              </div>
            </div>
          </div>`;
      }

      return `
        <!-- Drawer Header -->
        <div class="drawer-header">
          <div class="drawer-meta-pills">
            <div class="drawer-subsystem-badge" style="border-left-color: ${subColor};">
              <span class="badge-dot" style="background-color: ${subColor};"></span>
              <span class="badge-text">S${String(node.subsystem_id).padStart(2, '0')}: ${subInfo.name}</span>
            </div>
            <span class="drawer-tier-pill tier-${(node.tier_level || '').toLowerCase()}">${tierFormatted}</span>
            <span class="drawer-category-pill">${categoryFormatted}</span>
            <button class="drawer-close-btn" title="Close drawer (Escape)" aria-label="Close drawer">×</button>
          </div>

          <div class="drawer-title-row">
            <h2 id="drawer-node-title" class="drawer-title">${node.name || node.id}</h2>
            <div class="drawer-subtitle-row">
              <code class="drawer-node-id">${node.id}</code>
              <span class="drawer-loc-pill">${node.loc || 0} LOC</span>
            </div>
          </div>

          <!-- Quick Action Buttons -->
          <div class="drawer-quick-actions">
            <button class="quick-action-btn" data-action="center-camera" title="Center camera on node in canvas">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/>
                <line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/>
              </svg>
              <span>Center Camera</span>
            </button>
            <button class="quick-action-btn" data-action="isolate-tree" title="Isolate hierarchical lineage in tree view">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
              </svg>
              <span>🌿 Isolate Tree</span>
            </button>
            <button class="quick-action-btn copy-path-btn" data-action="copy-path" title="Copy code file path to clipboard">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
              <span>Copy Code Path</span>
            </button>
            ${crossLinkTargetId ? `
              <button class="quick-action-btn cross-link-btn" data-action="cross-link" data-target-id="${crossLinkTargetId}" title="${crossLinkTitle}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
                </svg>
                <span>${crossLinkLabel}</span>
              </button>
            ` : ''}
          </div>

          <!-- Master Lens Segmented Toggle -->
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
        </div>

        <!-- Drawer Scrollable Content Body -->
        <div class="drawer-body">
          <!-- View 1: Plain English (ELI5) -->
          <div class="eli5-container" role="tabpanel">
            <div class="eli5-card">
              <h4 class="drawer-section-title"><span class="section-icon">📖</span> Core Concept & Summary</h4>
              <p class="eli5-summary-text">${eli5Summary}</p>
            </div>

            <div class="eli5-card analogy-card">
              <h4 class="drawer-section-title"><span class="section-icon">💡</span> Intuitive Real-World Analogy</h4>
              <p class="analogy-text">${intuitiveAnalogy}</p>
            </div>

            <div class="eli5-card protection-card">
              <h4 class="drawer-section-title"><span class="section-icon">🛡️</span> Why This Exists & How It Protects Money</h4>
              <p class="protection-text">${capitalProtection}</p>
            </div>

            <div class="eli5-card example-card">
              <h4 class="drawer-section-title"><span class="section-icon">⚡</span> Simplified Practical Trading Flow</h4>
              <p class="example-text">${tradingExample}</p>
            </div>
          </div>

          <!-- View 2: Institutional Quant Rigor -->
          <div class="quant-container" role="tabpanel" style="display: none;">
            <div class="quant-card">
              <h4 class="drawer-section-title"><span class="section-icon">📐</span> Mathematical Formulation & LaTeX Rigor</h4>
              ${mathHtml || '<p class="empty-math-text">No explicit mathematical formulation provided for this gateway node.</p>'}
            </div>

            ${variablesHtml}
            ${proofHtml}
          </div>

          <!-- Section: Data Contract & System Wiring -->
          <div class="data-contract-section">
            <h3 class="drawer-section-title"><span class="section-icon">🔌</span> Data Contract & Component Telemetry</h3>
            <div class="contract-grid">
              <div class="contract-column">
                <span class="contract-col-heading">Consumed Inputs:</span>
                <div class="contract-chips-flow">${inputsHtml}</div>
              </div>
              <div class="contract-column">
                <span class="contract-col-heading">Emitted Signals:</span>
                <div class="contract-chips-flow">${outputsHtml}</div>
              </div>
            </div>
          </div>

          <!-- Section: Interactive Dependency Explorer -->
          <div class="dependency-explorer">
            <h3 class="drawer-section-title"><span class="section-icon">🕸️</span> Interactive Dependency Explorer</h3>
            <div class="dependency-grid">
              <div class="dependency-col caller-col">
                <span class="dep-col-heading emerald-text">← Who Depends On This (${(node.callers || []).length}):</span>
                <div class="dep-tags-flow">${callersHtml}</div>
              </div>
              <div class="dependency-col dep-col">
                <span class="dep-col-heading rose-text">→ What This Uses (${(node.dependencies || []).length}):</span>
                <div class="dep-tags-flow">${depsHtml}</div>
              </div>
            </div>
          </div>

          <!-- Section: Code Realization Viewer -->
          ${codeViewerHtml}

          <!-- Section: Failure Modes & Circuit Breakers -->
          ${failureHtml}

          <!-- Section: Citations & Academic Grounding -->
          ${citationsHtml}
        </div>
      `;
    }
  }

  /**
   * Factory function to initialize and mount DeepDiveDrawer.
   * @param {Object} canvasEngine
   * @param {Object} graphData
   * @returns {DeepDiveDrawer}
   */
  function initDeepDiveDrawer(canvasEngine, graphData) {
    return new DeepDiveDrawer(canvasEngine, graphData);
  }

  // Universal Module Exports (Node.js CommonJS & Browser Window)
  const exportsObj = {
    DeepDiveDrawer,
    initDeepDiveDrawer,
    formatMathToHtml,
    highlightCode,
    SUBSYSTEM_INFO,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exportsObj;
  }
  if (typeof global !== 'undefined') {
    Object.assign(global, exportsObj);
  }
})(typeof window !== 'undefined' ? window : globalThis);
