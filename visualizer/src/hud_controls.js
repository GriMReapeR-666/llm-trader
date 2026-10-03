/**
 * Cyber-Quant HyperGraph Studio - Interactive HUD Controls & Command Center
 *
 * Provides institutional Bloomberg/OLED terminal floating HUDs:
 * - Top command bar with brand badge, AST status, perspective switcher, layout selector, camera tools
 * - Global fuzzy search autocomplete with keyboard shortcuts ('/' and 'Ctrl+K'), arrow navigation, camera lock
 * - Subsystem filter checklist with tri-state select/deselect all and domain Solo isolation
 * - Active dependency legend pills (Emerald callers, Rose dependencies, Violet grounding)
 * - Frosted glass hover card tooltip displaying live node metrics and ELI5 summaries
 *
 * Zero external dependencies.
 */

(function (global) {
  'use strict';

  const SUBSYSTEM_COLORS = {
    1: '#38bdf8',  // Market Data & Macro Ingestion
    2: '#818cf8',  // Macro & Regime Classification
    3: '#34d399',  // Alpha Mining & Factor Discovery
    4: '#c084fc',  // Multi-Agent LLM Deliberation
    5: '#f472b6',  // Causal Inference & DAG Discovery
    6: '#fb923c',  // Overfit & Deflated Sharpe Gating
    7: '#f87171',  // Quantitative Risk Allocation
    8: '#a78bfa',  // Continual Learning & Meta-loop
    9: '#4ade80',  // Core MT5 Execution Gateway
    10: '#facc15', // Guardian Sentinel & Circuit Breakers
    11: '#2dd4bf', // Memory Persistence & State Store
    12: '#60a5fa', // Mission Control & Observability
  };

  const DEFAULT_COLOR = '#94a3b8';

  /**
   * Fast fuzzy search ranking nodes against query across multiple fields.
   * Matches node ID, name, subsystem name, plain English summary, and code path.
   *
   * @param {Array<Object>} nodes Graph nodes array
   * @param {string} query Search input query
   * @param {number} [limit=10] Maximum results returned
   * @returns {Array<Object>} Ranked array of node objects
   */
  function fuzzySearch(nodes, query, limit = 10) {
    if (!Array.isArray(nodes) || !query || typeof query !== 'string') {
      return [];
    }

    const q = query.trim().toLowerCase();
    if (!q) return [];

    const scored = [];

    for (const node of nodes) {
      const id = (node.id || '').toLowerCase();
      const name = (node.name || '').toLowerCase();
      const summary = (node.plain_english_summary || '').toLowerCase();
      // Search both the real implementing file and the originally declared
      // module path (provenance) so re-pointed nodes stay findable by name.
      const codePath = [
        (node.code_realization && node.code_realization.file_path) || '',
        (node.code_realization && node.code_realization.declared_path) || '',
        node.code_file_path || '',
      ].join(' ').toLowerCase();
      const subName = (node.subsystem_name || '').toLowerCase();

      let score = 0;

      // Exact matches
      if (id === q || name === q) {
        score += 1000;
      } else if (id.startsWith(q) || name.startsWith(q)) {
        score += 500;
      } else if (id.includes(q)) {
        score += 300;
      } else if (name.includes(q)) {
        score += 250;
      }

      // Keyword matches
      if (codePath.includes(q)) {
        score += 200;
      }
      if (subName.includes(q)) {
        score += 120;
      }
      if (summary.includes(q)) {
        score += 80;
      }

      // Subsequence fuzzy match for abbreviations (e.g. "mt5", "ewc", "dsr")
      if (score === 0 && q.length >= 2) {
        let nIdx = 0;
        let matchedChars = 0;
        for (let i = 0; i < id.length && nIdx < q.length; i++) {
          if (id[i] === q[nIdx]) {
            matchedChars++;
            nIdx++;
          }
        }
        if (matchedChars === q.length) {
          score += 40;
        }
      }

      if (score > 0) {
        scored.push({ node, score });
      }
    }

    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, limit).map(item => item.node);
  }

  /**
   * HUDControls orchestrator mounting floating interface panels onto document.
   */
  class HUDControls {
    constructor(canvasEngine, graphData, container = null) {
      this.engine = canvasEngine;
      this.data = graphData || (canvasEngine && canvasEngine.data) || { nodes: [], edges: [], subsystems: [] };
      this.container = container || (typeof document !== 'undefined' ? document.body : null);

      this.currentPerspective = 'code';
      this.selectedSearchIndex = -1;
      this.searchResults = [];
      this.knowledgeMatrix = null;

      // Callbacks
      this.onPerspectiveChange = null;
      this.onNodeSelected = null;

      this._init();
    }

    setKnowledgeMatrix(matrix) {
      this.knowledgeMatrix = matrix;
      if (matrix && typeof matrix.setHUDControls === 'function' && matrix.hudControls !== this) {
        matrix.setHUDControls(this);
      }
    }

    setPerspective(perspectiveId) {
      if (this.currentPerspective === perspectiveId) return;
      this.currentPerspective = perspectiveId;
      if (this.container) {
        const btns = this.container.querySelectorAll('.perspective-btn');
        btns.forEach(b => {
          if (b.getAttribute('data-perspective') === perspectiveId) {
            b.classList.add('active');
          } else {
            b.classList.remove('active');
          }
        });
      }

      // Two-way synchronization with Knowledge Matrix
      if (perspectiveId === 'matrix') {
        if (this.knowledgeMatrix && !this.knowledgeMatrix.isOpen && typeof this.knowledgeMatrix.openMatrix === 'function') {
          this.knowledgeMatrix.openMatrix();
        }
      } else {
        if (this.knowledgeMatrix && this.knowledgeMatrix.isOpen && typeof this.knowledgeMatrix.closeMatrix === 'function') {
          this.knowledgeMatrix.closeMatrix();
        }
      }

      if (this.onPerspectiveChange) {
        this.onPerspectiveChange(perspectiveId);
      }
    }

    _init() {
      if (!this.container || typeof document === 'undefined') return;

      this._mountTopCommandBar();
      this._mountSubsystemFilterPanel();
      this._mountActiveDependencyLegend();
      this._mountNodeHoverTooltip();
      this._bindGlobalKeyboardShortcuts();
      this._bindEngineCallbacks();
    }

    _mountTopCommandBar() {
      let topBar = this.container.querySelector('.top-command-bar');
      if (!topBar) {
        topBar = document.createElement('header');
        topBar.className = 'top-command-bar';
        this.container.appendChild(topBar);
      } else {
        topBar.innerHTML = '';
      }

      // 1. Brand Section
      const brandSection = document.createElement('div');
      brandSection.className = 'brand-section';

      const brandBadge = document.createElement('div');
      brandBadge.className = 'brand-badge';
      brandBadge.id = 'brand-home-btn';
      brandBadge.setAttribute('title', 'Reset to Global Architecture Overview');
      brandBadge.innerHTML = `
        <div class="brand-icon">
          <svg viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
        </div>
        <div class="brand-titles">
          <span class="brand-title">Cyber-Quant <span style="color:var(--color-sky);">HyperGraph</span></span>
          <span class="brand-subtitle">Autonomous Institutional Trader</span>
        </div>
      `;
      brandBadge.addEventListener('click', () => {
        if (this.engine) this.engine.resetCamera();
      });

      const statusTag = document.createElement('div');
      statusTag.className = 'status-tag';
      statusTag.innerHTML = `<span class="status-dot"></span><span>SCHEMA V22 · 100% CLEAN AST</span>`;

      brandSection.appendChild(brandBadge);
      brandSection.appendChild(statusTag);
      topBar.appendChild(brandSection);

      // 2. Perspective Switcher
      const perspectiveSwitcher = document.createElement('div');
      perspectiveSwitcher.className = 'perspective-switcher';

      const perspectives = [
        { id: 'code', icon: '⚙️', label: 'Code', title: 'Code Architecture Dependency Graph' },
        { id: 'quant', icon: '📐', label: 'Quant', title: 'Quant Knowledge & Theory Atlas' },
        { id: 'tour', icon: '🚀', label: 'Lifecycle Tour', title: '7-Stage Interactive Trade Lifecycle Simulator' },
        { id: 'matrix', icon: '📚', label: 'Matrix', title: 'Searchable Knowledge Matrix & Formula Library' },
      ];

      perspectives.forEach(p => {
        const btn = document.createElement('button');
        btn.className = `perspective-btn ${p.id === this.currentPerspective ? 'active' : ''}`;
        btn.setAttribute('data-perspective', p.id);
        btn.setAttribute('title', p.title);
        btn.innerHTML = `<span>${p.icon}</span> ${p.label}`;
        btn.addEventListener('click', () => {
          this.setPerspective(p.id);
        });
        perspectiveSwitcher.appendChild(btn);
      });
      topBar.appendChild(perspectiveSwitcher);

      // 3. Layout Selector
      const layoutSelector = document.createElement('div');
      layoutSelector.className = 'layout-selector';

      const layouts = [
        { id: 'islands', label: '🏝️ Islands', title: '12 Operational Domain Clusters' },
        { id: 'pipeline', label: '📊 Pipeline', title: 'Chronological Trade Pipeline Flow' },
        { id: 'radar', label: '🎯 Radar', title: 'Concentric Dependency Orbit' },
        { id: 'tree', label: '🌿 Tree', title: 'Hierarchical Lineage Tree' },
        { id: 'force', label: '⚛️ Force', title: 'Dynamic Physics Spring Layout' },
      ];

      layouts.forEach((l, idx) => {
        const btn = document.createElement('button');
        btn.className = `layout-btn ${idx === 0 ? 'active' : ''}`;
        btn.setAttribute('data-layout', l.id);
        btn.setAttribute('title', l.title);
        btn.textContent = l.label;
        btn.addEventListener('click', () => {
          layoutSelector.querySelectorAll('.layout-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          if (this.engine) {
            if (typeof this.engine.switchLayout === 'function') {
              this.engine.switchLayout(l.id);
            } else if (typeof this.engine.setLayout === 'function') {
              this.engine.setLayout(l.id);
            }
          }
        });
        layoutSelector.appendChild(btn);
      });
      topBar.appendChild(layoutSelector);

      // 4. Quick Search Container
      const searchContainer = document.createElement('div');
      searchContainer.className = 'search-container';
      searchContainer.id = 'search';

      const searchWrapper = document.createElement('div');
      searchWrapper.className = 'search-input-wrapper';

      const searchInput = document.createElement('input');
      searchInput.type = 'text';
      searchInput.id = 'quick-search-input';
      searchInput.placeholder = 'Search code, quant models, formulas...';
      searchInput.setAttribute('autocomplete', 'off');
      searchInput.setAttribute('spellcheck', 'false');

      const shortcutBadge = document.createElement('span');
      shortcutBadge.className = 'search-shortcut-badge';
      shortcutBadge.textContent = '/ or ⌘K';

      searchWrapper.appendChild(searchInput);
      searchWrapper.appendChild(shortcutBadge);

      const searchDropdown = document.createElement('div');
      searchDropdown.id = 'search-results-dropdown';
      searchDropdown.className = 'search-dropdown';

      searchContainer.appendChild(searchWrapper);
      searchContainer.appendChild(searchDropdown);
      topBar.appendChild(searchContainer);

      this._bindSearchInput(searchInput, searchDropdown);

      // 5. Camera Controls
      const cameraControls = document.createElement('div');
      cameraControls.className = 'camera-controls';

      const btnZoomIn = document.createElement('button');
      btnZoomIn.className = 'camera-btn';
      btnZoomIn.id = 'btn-zoom-in';
      btnZoomIn.setAttribute('title', 'Zoom In (+)');
      btnZoomIn.textContent = '+';
      btnZoomIn.addEventListener('click', () => {
        if (this.engine && typeof this.engine.zoomAtScreen === 'function') {
          this.engine.zoomAtScreen(this.engine.width / 2, this.engine.height / 2, 1.25);
        }
      });

      const btnZoomOut = document.createElement('button');
      btnZoomOut.className = 'camera-btn';
      btnZoomOut.id = 'btn-zoom-out';
      btnZoomOut.setAttribute('title', 'Zoom Out (−)');
      btnZoomOut.textContent = '−';
      btnZoomOut.addEventListener('click', () => {
        if (this.engine && typeof this.engine.zoomAtScreen === 'function') {
          this.engine.zoomAtScreen(this.engine.width / 2, this.engine.height / 2, 0.8);
        }
      });

      const btnReset = document.createElement('button');
      btnReset.className = 'camera-btn';
      btnReset.id = 'btn-reset-camera';
      btnReset.setAttribute('title', 'Reset Camera View (⟲)');
      btnReset.textContent = '⟲';
      btnReset.addEventListener('click', () => {
        if (this.engine && typeof this.engine.resetCamera === 'function') {
          this.engine.resetCamera();
        }
      });

      const btnFullscreen = document.createElement('button');
      btnFullscreen.className = 'camera-btn';
      btnFullscreen.id = 'btn-fullscreen';
      btnFullscreen.setAttribute('title', 'Toggle Fullscreen (F11)');
      btnFullscreen.textContent = '⛶';
      btnFullscreen.addEventListener('click', () => {
        if (typeof document !== 'undefined') {
          if (!document.fullscreenElement) {
            if (document.documentElement && document.documentElement.requestFullscreen) {
              document.documentElement.requestFullscreen();
            }
          } else {
            if (document.exitFullscreen) {
              document.exitFullscreen();
            }
          }
        }
      });

      cameraControls.appendChild(btnZoomIn);
      cameraControls.appendChild(btnZoomOut);
      cameraControls.appendChild(btnReset);
      cameraControls.appendChild(btnFullscreen);
      topBar.appendChild(cameraControls);
    }

    _bindSearchInput(input, dropdown) {
      input.addEventListener('input', () => {
        const query = input.value;
        const nodes = (this.engine && this.engine.nodes) || this.data.nodes || [];
        this.searchResults = fuzzySearch(nodes, query, 10);
        this.selectedSearchIndex = this.searchResults.length > 0 ? 0 : -1;
        this._renderSearchResults(dropdown);
      });

      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') {
          if (this.searchResults.length > 0) {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            this.selectedSearchIndex = (this.selectedSearchIndex + 1) % this.searchResults.length;
            this._updateSelectedSearchItem(dropdown);
          }
        } else if (e.key === 'ArrowUp') {
          if (this.searchResults.length > 0) {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            this.selectedSearchIndex = (this.selectedSearchIndex - 1 + this.searchResults.length) % this.searchResults.length;
            this._updateSelectedSearchItem(dropdown);
          }
        } else if (e.key === 'Enter') {
          if (this.selectedSearchIndex >= 0 && this.selectedSearchIndex < this.searchResults.length) {
            if (typeof e.preventDefault === 'function') e.preventDefault();
            const chosen = this.searchResults[this.selectedSearchIndex];
            this._selectNodeFromSearch(chosen, input, dropdown);
          }
        } else if (e.key === 'Escape') {
          this._closeSearchDropdown(dropdown);
          if (typeof input.blur === 'function') input.blur();
        }
      });

      // Close dropdown when clicking outside
      if (typeof document !== 'undefined') {
        document.addEventListener('click', (e) => {
          if (input.contains && dropdown.contains) {
            if (!input.contains(e.target) && !dropdown.contains(e.target)) {
              this._closeSearchDropdown(dropdown);
            }
          }
        });
      }
    }

    _renderSearchResults(dropdown) {
      dropdown.innerHTML = '';

      if (this.searchResults.length === 0) {
        const emptyMsg = document.createElement('div');
        emptyMsg.className = 'search-empty-msg';
        emptyMsg.textContent = 'No matching components or formulas';
        dropdown.appendChild(emptyMsg);
        dropdown.classList.add('visible');
        dropdown.style.display = 'block';
        return;
      }

      this.searchResults.forEach((node, idx) => {
        const item = document.createElement('div');
        item.className = `search-result-item ${idx === this.selectedSearchIndex ? 'selected active' : ''}`;
        item.setAttribute('data-index', String(idx));
        item.setAttribute('data-node-id', node.id);

        const subColor = SUBSYSTEM_COLORS[node.subsystem_id] || DEFAULT_COLOR;
        const callersCount = (node.callers || []).length;
        const depsCount = (node.dependencies || []).length;

        item.innerHTML = `
          <div class="search-result-header">
            <span class="search-result-title">
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background-color:${subColor};"></span>
              ${node.name || node.id}
            </span>
            <span class="search-result-domain" style="border-left: 2px solid ${subColor};">
              ${node.subsystem_name || `Domain ${node.subsystem_id}`}
            </span>
          </div>
          <div class="search-result-meta">
            <span>Tier: ${node.tier_level || node.tier || 'L1'}</span>
            <span>•</span>
            <span>← ${callersCount} callers</span>
            <span>•</span>
            <span>→ ${depsCount} deps</span>
          </div>
          <div class="search-result-summary">${node.plain_english_summary || ''}</div>
        `;

        item.addEventListener('click', () => {
          const input = this.container.querySelector('#quick-search-input');
          this._selectNodeFromSearch(node, input, dropdown);
        });

        dropdown.appendChild(item);
      });

      dropdown.classList.add('visible');
      dropdown.style.display = 'block';
    }

    _updateSelectedSearchItem(dropdown) {
      const items = dropdown.querySelectorAll('.search-result-item');
      items.forEach((item, idx) => {
        if (idx === this.selectedSearchIndex) {
          item.classList.add('selected');
          item.classList.add('active');
          if (item.scrollIntoViewIfNeeded) {
            item.scrollIntoViewIfNeeded(false);
          }
        } else {
          item.classList.remove('selected');
          item.classList.remove('active');
        }
      });
    }

    _selectNodeFromSearch(node, input, dropdown) {
      if (!node) return;
      if (input) input.value = node.name || node.id;
      this._closeSearchDropdown(dropdown);

      if (this.engine) {
        if (typeof this.engine.focusNode === 'function') {
          this.engine.focusNode(node.id);
        }
        if (typeof this.engine.selectNode === 'function') {
          this.engine.selectNode(node.id);
        }
      }

      if (this.onNodeSelected) {
        this.onNodeSelected(node);
      }
    }

    _closeSearchDropdown(dropdown) {
      if (!dropdown) return;
      dropdown.classList.remove('visible');
      dropdown.style.display = 'none';
      this.searchResults = [];
      this.selectedSearchIndex = -1;
    }

    _mountSubsystemFilterPanel() {
      let panel = this.container.querySelector('#subsystem-filter-panel');
      if (!panel) {
        panel = document.createElement('div');
        panel.id = 'subsystem-filter-panel';
        this.container.appendChild(panel);
      } else {
        panel.innerHTML = '';
      }

      const subsystems = this.data.subsystems || [];
      const nodes = (this.engine && this.engine.nodes) || this.data.nodes || [];

      // Calculate node counts per subsystem
      const counts = {};
      for (const n of nodes) {
        counts[n.subsystem_id] = (counts[n.subsystem_id] || 0) + 1;
      }

      // Filter header
      const header = document.createElement('div');
      header.className = 'filter-header';

      const title = document.createElement('div');
      title.className = 'filter-title';
      title.innerHTML = `<span>Operational Domains</span>`;

      const actions = document.createElement('div');
      actions.className = 'filter-header-actions';

      const toggleAllLabel = document.createElement('label');
      toggleAllLabel.className = 'filter-all-label';

      const toggleAll = document.createElement('input');
      toggleAll.type = 'checkbox';
      toggleAll.id = 'filter-toggle-all';
      toggleAll.checked = true;

      const toggleAllText = document.createElement('span');
      toggleAllText.textContent = 'All';

      toggleAllLabel.appendChild(toggleAll);
      toggleAllLabel.appendChild(toggleAllText);

      const collapseBtn = document.createElement('button');
      collapseBtn.className = 'filter-collapse-btn';
      collapseBtn.id = 'filter-collapse-toggle';
      collapseBtn.setAttribute('title', 'Collapse Panel');
      collapseBtn.textContent = '▾';
      collapseBtn.addEventListener('click', () => {
        panel.classList.toggle('collapsed');
        collapseBtn.textContent = panel.classList.contains('collapsed') ? '▴' : '▾';
      });

      actions.appendChild(toggleAllLabel);
      actions.appendChild(collapseBtn);
      header.appendChild(title);
      header.appendChild(actions);
      panel.appendChild(header);

      // Subsystem items list
      const list = document.createElement('div');
      list.className = 'subsystem-list';

      const checkboxes = [];

      for (let id = 1; id <= 12; id++) {
        const sub = subsystems.find(s => s.id === id) || { id, name: `Domain ${id}` };
        const color = SUBSYSTEM_COLORS[id] || DEFAULT_COLOR;
        const count = counts[id] || 0;

        const item = document.createElement('div');
        item.className = 'subsystem-item';
        item.setAttribute('data-subsystem-id', String(id));

        const leftLabel = document.createElement('label');
        leftLabel.className = 'subsystem-item-left';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'subsystem-checkbox';
        cb.setAttribute('data-subsystem-id', String(id));
        cb.checked = true;
        checkboxes.push(cb);

        cb.addEventListener('change', () => {
          if (!this.engine.hiddenSubsystems) {
            this.engine.hiddenSubsystems = new Set();
          }
          if (cb.checked) {
            this.engine.hiddenSubsystems.delete(id);
          } else {
            this.engine.hiddenSubsystems.add(id);
          }
          this._updateToggleAllState(toggleAll, checkboxes);
        });

        const dot = document.createElement('span');
        dot.className = 'subsystem-dot';
        dot.style.backgroundColor = color;

        const nameSpan = document.createElement('span');
        nameSpan.className = 'subsystem-name';
        nameSpan.setAttribute('title', sub.name);
        nameSpan.textContent = sub.name;

        leftLabel.appendChild(cb);
        leftLabel.appendChild(dot);
        leftLabel.appendChild(nameSpan);

        const rightDiv = document.createElement('div');
        rightDiv.className = 'subsystem-item-right';

        const countBadge = document.createElement('span');
        countBadge.className = 'subsystem-count';
        countBadge.textContent = String(count);

        const soloBtn = document.createElement('button');
        soloBtn.className = 'solo-btn';
        soloBtn.setAttribute('data-subsystem-id', String(id));
        soloBtn.setAttribute('title', 'Isolate this domain');
        soloBtn.textContent = 'Solo';

        soloBtn.addEventListener('click', (e) => {
          if (typeof e.stopPropagation === 'function') e.stopPropagation();
          if (!this.engine.hiddenSubsystems) {
            this.engine.hiddenSubsystems = new Set();
          }

          this.engine.hiddenSubsystems.clear();
          for (let sid = 1; sid <= 12; sid++) {
            if (sid !== id) {
              this.engine.hiddenSubsystems.add(sid);
            }
          }

          checkboxes.forEach(chk => {
            const chkId = parseInt(chk.getAttribute('data-subsystem-id'), 10);
            chk.checked = (chkId === id);
          });

          this._updateToggleAllState(toggleAll, checkboxes);
        });

        rightDiv.appendChild(countBadge);
        rightDiv.appendChild(soloBtn);

        item.appendChild(leftLabel);
        item.appendChild(rightDiv);
        list.appendChild(item);
      }

      panel.appendChild(list);

      // Toggle All handler
      toggleAll.addEventListener('change', () => {
        const checkState = toggleAll.checked;
        if (!this.engine.hiddenSubsystems) {
          this.engine.hiddenSubsystems = new Set();
        }

        checkboxes.forEach(chk => {
          chk.checked = checkState;
          const sid = parseInt(chk.getAttribute('data-subsystem-id'), 10);
          if (checkState) {
            this.engine.hiddenSubsystems.delete(sid);
          } else {
            this.engine.hiddenSubsystems.add(sid);
          }
        });
        toggleAll.indeterminate = false;
      });
    }

    _updateToggleAllState(toggleAll, checkboxes) {
      if (!toggleAll) return;
      let checkedCount = 0;
      checkboxes.forEach(cb => {
        if (cb.checked) checkedCount++;
      });

      if (checkedCount === checkboxes.length) {
        toggleAll.checked = true;
        toggleAll.indeterminate = false;
      } else if (checkedCount === 0) {
        toggleAll.checked = false;
        toggleAll.indeterminate = false;
      } else {
        toggleAll.checked = false;
        toggleAll.indeterminate = true;
      }
    }

    _mountActiveDependencyLegend() {
      let legend = this.container.querySelector('#active-dependency-legend');
      if (!legend) {
        legend = document.createElement('div');
        legend.id = 'active-dependency-legend';
        this.container.appendChild(legend);
      } else {
        legend.innerHTML = '';
      }

      const pills = [
        { type: 'incoming', label: '← Incoming Callers', title: 'Subsystems calling or requesting data from selected component' },
        { type: 'outgoing', label: '→ Outgoing Dependencies', title: 'Subsystems imported and executed by selected component' },
        { type: 'grounding', label: '✦ Theoretical Grounding', title: 'Theoretical mathematical theorems & proofs cross-linked to code' },
      ];

      pills.forEach(p => {
        const pill = document.createElement('span');
        pill.className = `legend-pill ${p.type}`;
        pill.setAttribute('title', p.title);
        pill.innerHTML = `<span class="legend-dot"></span><span>${p.label}</span>`;
        legend.appendChild(pill);
      });
    }

    _mountNodeHoverTooltip() {
      let tooltip = this.container.querySelector('#node-hover-tooltip');
      if (!tooltip) {
        tooltip = document.createElement('div');
        tooltip.id = 'node-hover-tooltip';
        tooltip.className = 'hover-card-tooltip';
        this.container.appendChild(tooltip);
      }
    }

    _bindEngineCallbacks() {
      if (!this.engine) return;

      const prevHover = this.engine.onNodeHover;
      this.engine.onNodeHover = (node) => {
        if (prevHover) prevHover(node);
        this._updateTooltip(node);
      };
    }

    _updateTooltip(node) {
      const tooltip = this.container.querySelector('#node-hover-tooltip');
      if (!tooltip) return;

      if (!node) {
        tooltip.classList.remove('visible');
        tooltip.style.display = 'none';
        return;
      }

      const subColor = SUBSYSTEM_COLORS[node.subsystem_id] || DEFAULT_COLOR;
      const callersCount = (node.callers || []).length;
      const depsCount = (node.dependencies || []).length;
      const subName = node.subsystem_name || `Domain ${node.subsystem_id}`;

      tooltip.innerHTML = `
        <div class="tooltip-header">
          <span class="tooltip-title">${node.name || node.id}</span>
          <span class="tooltip-domain-tag" style="background: ${subColor}22; color: ${subColor}; border: 1px solid ${subColor}55;">
            ${subName}
          </span>
        </div>
        <div class="tooltip-meta-row">
          <span class="tooltip-meta-item">Tier: <strong style="color:#ffffff;">${node.tier_level || node.tier || 'L1'}</strong></span>
          <span>•</span>
          <span class="tooltip-meta-item">Callers: <strong style="color:${SUBSYSTEM_COLORS[3]};">${callersCount}</strong></span>
          <span>•</span>
          <span class="tooltip-meta-item">Deps: <strong style="color:${SUBSYSTEM_COLORS[7]};">${depsCount}</strong></span>
        </div>
        <div class="tooltip-summary">
          ${node.plain_english_summary || 'Component operational node in autonomous trading engine.'}
        </div>
      `;

      // Position tooltip near cursor on screen
      if (this.engine && typeof this.engine.worldToScreen === 'function' && typeof node.x === 'number') {
        const screenPos = this.engine.worldToScreen(node.x, node.y);
        const winW = (typeof window !== 'undefined' && window.innerWidth) || 1280;
        const winH = (typeof window !== 'undefined' && window.innerHeight) || 720;
        const tipX = Math.min(winW - 300, Math.max(16, screenPos.x + 20));
        const tipY = Math.min(winH - 150, Math.max(70, screenPos.y - 20));
        tooltip.style.left = `${tipX}px`;
        tooltip.style.top = `${tipY}px`;
      }

      tooltip.classList.add('visible');
      tooltip.style.display = 'block';
    }

    _bindGlobalKeyboardShortcuts() {
      if (typeof window === 'undefined') return;

      window.addEventListener('keydown', (e) => {
        const activeTag = (document.activeElement && document.activeElement.tagName) || '';
        if ((e.key === '/' && activeTag !== 'INPUT' && activeTag !== 'TEXTAREA') ||
            ((e.ctrlKey || e.metaKey) && e.key && e.key.toLowerCase() === 'k')) {
          if (typeof e.preventDefault === 'function') e.preventDefault();
          const input = this.container.querySelector('#quick-search-input');
          if (input) {
            if (typeof input.focus === 'function') input.focus();
            if (typeof input.select === 'function') input.select();
          }
        }
      });
    }
  }

  /**
   * Factory function to initialize and mount HUD onto canvasEngine and DOM container.
   */
  function initHUD(canvasEngine, graphData, container = null) {
    return new HUDControls(canvasEngine, graphData, container);
  }

  // Universal Module Export (Browser & CommonJS / Node)
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      HUDControls,
      initHUD,
      fuzzySearch,
      SUBSYSTEM_COLORS,
    };
  }

  if (typeof global !== 'undefined') {
    global.HUDControls = HUDControls;
    global.initHUD = initHUD;
    global.fuzzySearch = fuzzySearch;
  }
})(typeof window !== 'undefined' ? window : globalThis);
