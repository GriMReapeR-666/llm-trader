/**
 * Cyber-Quant HyperGraph Studio - Searchable Knowledge Matrix & Quant Encyclopedia Library Mode
 *
 * Provides a full-screen institutional matrix catalog of all 109 core quantitative modules,
 * theories, agent roles, and protective circuit breakers with multi-dimensional filtering
 * (by Domain, Complexity, Category, Text search, and Sorting) and seamless transitions
 * to the graph canvas and universal deep-dive drawer.
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

  const CATEGORY_LABELS = {
    all: 'All Categories',
    quant_theory: 'Quant Theories',
    code_module: 'Code Modules',
    agent_role: 'Agent Roles',
    protective_gate: 'Protective Gates',
  };

  const COMPLEXITY_LABELS = {
    all: 'All Complexity Levels',
    foundational: 'Foundational (Beginner)',
    intermediate: 'Intermediate (Strategy)',
    advanced: 'Advanced Quant (PhD/Institutional)',
  };

  const DEFAULT_FILTERS = {
    search: '',
    domain: 'all',
    complexity: 'all',
    category: 'all',
    sort: 'name',
  };

  /**
   * Helper to map a node to one of three normalized complexity tiers:
   * 'foundational', 'intermediate', 'advanced'
   */
  function getNodeComplexity(node) {
    if (!node) return 'intermediate';
    if (node.complexity) {
      const c = String(node.complexity).toLowerCase();
      if (c.includes('found') || c.includes('begin')) return 'foundational';
      if (c.includes('inter')) return 'intermediate';
      if (c.includes('adv') || c.includes('phd')) return 'advanced';
    }
    const tier = node.tier_level || node.tier || '';
    if (tier === 'L0_Gateway' || tier === 'L3_Persistence') {
      return 'foundational';
    }
    if (tier === 'L1_Orchestration') {
      return 'intermediate';
    }
    if (tier === 'L2_Strategy_Core' || node.category === 'quant_theory') {
      return 'advanced';
    }
    return 'intermediate';
  }

  /**
   * Fast text matching across all relevant node metadata fields.
   */
  function matchesSearchText(node, query) {
    if (!query) return true;
    const q = query.trim().toLowerCase();
    if (!q) return true;

    // 1. Basic properties
    if ((node.id || '').toLowerCase().includes(q)) return true;
    if ((node.name || '').toLowerCase().includes(q)) return true;
    if ((node.plain_english_summary || '').toLowerCase().includes(q)) return true;
    if ((node.subsystem_name || '').toLowerCase().includes(q)) return true;
    if ((node.category || '').toLowerCase().includes(q)) return true;
    if ((node.tier_level || '').toLowerCase().includes(q)) return true;

    // 2. Mathematical formulation
    if (node.mathematical_formulation) {
      const mf = node.mathematical_formulation;
      if ((mf.latex_equation || '').toLowerCase().includes(q)) return true;
      if ((mf.algorithmic_proof || '').toLowerCase().includes(q)) return true;
      if (mf.variables) {
        for (const [k, v] of Object.entries(mf.variables)) {
          if (k.toLowerCase().includes(q) || String(v).toLowerCase().includes(q)) return true;
        }
      }
    }

    // 3. Code realization
    if (node.code_realization) {
      const cr = node.code_realization;
      if ((cr.file_path || '').toLowerCase().includes(q)) return true;
      if ((cr.signature || '').toLowerCase().includes(q)) return true;
      if ((cr.code_snippet || '').toLowerCase().includes(q)) return true;
    }

    // 4. Citations & failure modes
    if (node.citations) {
      const cit = node.citations;
      if ((cit.source_title || '').toLowerCase().includes(q)) return true;
      if ((cit.video_id_or_doi || '').toLowerCase().includes(q)) return true;
      if ((cit.category || '').toLowerCase().includes(q)) return true;
    }
    if (node.failure_modes) {
      const fm = node.failure_modes;
      if ((fm.edge_case || '').toLowerCase().includes(q)) return true;
      if ((fm.defense_mechanism || '').toLowerCase().includes(q)) return true;
    }

    return false;
  }

  /**
   * KnowledgeMatrixManager Controller
   */
  class KnowledgeMatrixManager {
    constructor(canvasEngine, graphData, deepDiveDrawer = null, container = null) {
      this.engine = canvasEngine;
      this.data = graphData || (canvasEngine && canvasEngine.data) || { nodes: [], edges: [], subsystems: [] };
      this.drawer = deepDiveDrawer;
      this.container = container || (typeof document !== 'undefined' ? document.body : null);

      this.allNodes = Array.isArray(this.data.nodes) ? [...this.data.nodes] : [];
      this.subsystems = Array.isArray(this.data.subsystems) ? [...this.data.subsystems] : [];

      this.isOpen = false;
      this.hudControls = null;

      this.currentFilters = Object.assign({}, DEFAULT_FILTERS);
      this.filteredNodes = [...this.allNodes];

      // Callbacks
      this.onMatrixOpen = null;
      this.onMatrixClose = null;
      this.onOpenDeepDive = null;
      this.onFocusGraph = null;

      this._init();
    }

    setHUDControls(hud) {
      this.hudControls = hud;
    }

    openMatrix() {
      if (this.isOpen) return;
      this.isOpen = true;

      const viewEl = this._getViewElement();
      if (viewEl) {
        viewEl.classList.add('matrix-open');
        viewEl.classList.add('visible');
        viewEl.style.display = 'flex';
      }

      this.filterEntities(this.currentFilters);

      if (this.hudControls && typeof this.hudControls.setPerspective === 'function') {
        if (this.hudControls.currentPerspective !== 'matrix') {
          this.hudControls.setPerspective('matrix');
        }
      }

      if (this.onMatrixOpen) {
        this.onMatrixOpen();
      }
    }

    closeMatrix() {
      if (!this.isOpen) return;
      this.isOpen = false;

      const viewEl = this._getViewElement();
      if (viewEl) {
        viewEl.classList.remove('matrix-open');
        viewEl.classList.remove('visible');
        viewEl.style.display = 'none';
      }

      if (this.hudControls && typeof this.hudControls.setPerspective === 'function') {
        if (this.hudControls.currentPerspective === 'matrix') {
          this.hudControls.setPerspective('code');
        }
      }

      if (this.onMatrixClose) {
        this.onMatrixClose();
      }
    }

    filterEntities(filters = null) {
      if (filters && typeof filters === 'object') {
        this.currentFilters = Object.assign({}, DEFAULT_FILTERS, filters);
      } else if (!this.currentFilters) {
        this.currentFilters = Object.assign({}, DEFAULT_FILTERS);
      }

      const { search, domain, complexity, category, sort } = this.currentFilters;

      let res = this.allNodes.filter(node => {
        // 1. Search Query
        if (search && !matchesSearchText(node, search)) {
          return false;
        }

        // 2. Domain Filter
        if (domain && domain !== 'all') {
          const domId = Number(domain);
          if (node.subsystem_id !== domId) {
            return false;
          }
        }

        // 3. Category Filter
        if (category && category !== 'all') {
          const normCat = category.toLowerCase().replace(/s$/, '');
          const nodeCat = (node.category || '').toLowerCase().replace(/s$/, '');
          if (normCat !== nodeCat) {
            return false;
          }
        }

        // 4. Complexity Filter
        if (complexity && complexity !== 'all') {
          const normComp = complexity.toLowerCase();
          const nodeComp = getNodeComplexity(node);
          if (nodeComp !== normComp) {
            return false;
          }
        }

        return true;
      });

      // Sort
      if (sort === 'domain') {
        res.sort((a, b) => (a.subsystem_id || 0) - (b.subsystem_id || 0));
      } else if (sort === 'tier') {
        const tierOrder = {
          'L0_Gateway': 0,
          'L1_Orchestration': 1,
          'L2_Strategy_Core': 2,
          'L3_Persistence': 3,
        };
        res.sort((a, b) => (tierOrder[a.tier_level] ?? 99) - (tierOrder[b.tier_level] ?? 99));
      } else if (sort === 'callers') {
        res.sort((a, b) => ((b.callers || []).length) - ((a.callers || []).length));
      } else if (sort === 'dependencies') {
        res.sort((a, b) => ((b.dependencies || []).length) - ((a.dependencies || []).length));
      } else {
        // Default: alphabetical by Name
        res.sort((a, b) => {
          const nameA = (a.name || a.id || '').toLowerCase();
          const nameB = (b.name || b.id || '').toLowerCase();
          return nameA.localeCompare(nameB);
        });
      }

      this.filteredNodes = res;
      this._updateSummaryBadge();
      this.renderCards();

      return this.filteredNodes;
    }

    selectCard(nodeId) {
      const viewEl = this._getViewElement();
      if (!viewEl) return;
      const cards = viewEl.querySelectorAll('.matrix-card');
      cards.forEach(c => {
        if (c.getAttribute('data-node-id') === nodeId) {
          c.classList.add('selected');
          c.classList.add('active');
          if (c.scrollIntoViewIfNeeded) c.scrollIntoViewIfNeeded();
        } else {
          c.classList.remove('selected');
          c.classList.remove('active');
        }
      });
    }

    handleAction(action, nodeId) {
      if (action === 'deep-dive') {
        if (this.drawer && typeof this.drawer.openDrawer === 'function') {
          this.drawer.openDrawer(nodeId);
        }
        if (this.onOpenDeepDive) {
          this.onOpenDeepDive(nodeId);
        }
      } else if (action === 'focus-graph') {
        this.closeMatrix();

        if (this.engine) {
          if (typeof this.engine.focusNode === 'function') {
            this.engine.focusNode(nodeId);
          }
          if (typeof this.engine.selectNode === 'function') {
            this.engine.selectNode(nodeId);
          }
        }

        if (this.hudControls && typeof this.hudControls.setPerspective === 'function') {
          this.hudControls.setPerspective('code');
        }

        if (this.onFocusGraph) {
          this.onFocusGraph(nodeId);
        }
      }
    }

    renderCards() {
      const viewEl = this._getViewElement();
      if (!viewEl) return;

      const grid = viewEl.querySelector('#matrix-card-grid') || viewEl.querySelector('.matrix-card-grid');
      if (!grid) return;

      if (this.filteredNodes.length === 0) {
        grid.innerHTML = `
          <div class="matrix-empty-state">
            <div class="matrix-empty-icon">🔍</div>
            <h3 class="matrix-empty-title">No matching modules or theories found</h3>
            <p class="matrix-empty-subtitle">Try adjusting your filters or search terms.</p>
          </div>
        `;
        return;
      }

      let cardsHtml = '';
      for (const node of this.filteredNodes) {
        const subInfo = SUBSYSTEM_INFO[node.subsystem_id] || { name: node.subsystem_name || `Domain ${node.subsystem_id}`, color: DEFAULT_COLOR };
        const subColor = subInfo.color;
        const subName = node.subsystem_name || subInfo.name;
        const callersCount = (node.callers || []).length;
        const depsCount = (node.dependencies || []).length;
        const catLabel = CATEGORY_LABELS[node.category] || node.category || 'Module';
        const tierLabel = node.tier_level || node.tier || 'L1';
        const complexity = getNodeComplexity(node);
        const compLabel = COMPLEXITY_LABELS[complexity] || complexity;

        let formulaHtml = '';
        if (node.mathematical_formulation && node.mathematical_formulation.latex_equation) {
          const eq = node.mathematical_formulation.latex_equation;
          const cleanEq = eq.length > 55 ? eq.slice(0, 52) + '...' : eq;
          formulaHtml = `
            <div class="matrix-formula-chip" title="${eq}">
              <span class="formula-icon">∑</span>
              <span class="formula-text">${cleanEq}</span>
            </div>
          `;
        }

        cardsHtml += `
          <div class="matrix-card" data-node-id="${node.id}" data-domain="${node.subsystem_id}" data-category="${node.category || ''}" data-complexity="${complexity}">
            <div class="matrix-card-header">
              <div class="matrix-card-badges">
                <span class="matrix-domain-badge" style="background: ${subColor}22; color: ${subColor}; border: 1px solid ${subColor}55;">
                  <span class="matrix-domain-dot" style="background: ${subColor};"></span>
                  ${subName}
                </span>
                <span class="matrix-tier-badge">${tierLabel}</span>
                <span class="matrix-category-chip">${catLabel}</span>
              </div>
              <h3 class="matrix-card-title">${node.name || node.id}</h3>
            </div>
            <div class="matrix-card-body">
              <p class="matrix-card-summary">${node.plain_english_summary || 'Component operational node in autonomous trading engine.'}</p>
              ${formulaHtml}
            </div>
            <div class="matrix-card-stats">
              <span class="matrix-stat-item" title="Subsystems calling this node">
                <span class="matrix-stat-label">Callers:</span>
                <strong class="matrix-stat-val emerald">${callersCount}</strong>
              </span>
              <span class="matrix-stat-divider">•</span>
              <span class="matrix-stat-item" title="Dependencies imported by this node">
                <span class="matrix-stat-label">Deps:</span>
                <strong class="matrix-stat-val rose">${depsCount}</strong>
              </span>
              <span class="matrix-stat-divider">•</span>
              <span class="matrix-complexity-tag">${compLabel.split(' ')[0]}</span>
            </div>
            <div class="matrix-card-actions">
              <button class="matrix-action-btn matrix-deep-dive-btn" data-action="deep-dive" data-node-id="${node.id}">
                📖 Read Deep-Dive
              </button>
              <button class="matrix-action-btn matrix-focus-graph-btn" data-action="focus-graph" data-node-id="${node.id}">
                🎯 Focus in Graph
              </button>
            </div>
          </div>
        `;
      }

      grid.innerHTML = cardsHtml;
    }

    _getViewElement() {
      if (!this.container) return null;
      return this.container.querySelector('#knowledge-matrix-view');
    }

    _updateSummaryBadge() {
      const viewEl = this._getViewElement();
      if (!viewEl) return;
      const badge = viewEl.querySelector('.matrix-summary-badge');
      if (!badge) return;

      const total = this.allNodes.length;
      const quantCount = this.allNodes.filter(n => n.category === 'quant_theory').length;
      const domainCount = 12;

      badge.textContent = `${total} ENTITIES · ${quantCount} QUANT MODULES · ${domainCount} DOMAINS`;
    }

    _init() {
      if (!this.container || typeof document === 'undefined') return;

      let viewEl = this.container.querySelector('#knowledge-matrix-view');
      if (!viewEl) {
        viewEl = document.createElement('div');
        viewEl.id = 'knowledge-matrix-view';
        viewEl.className = 'knowledge-matrix-view';
        this.container.appendChild(viewEl);
      } else {
        viewEl.innerHTML = '';
      }

      // Build Inner Layout
      viewEl.innerHTML = `
        <div class="matrix-container">
          <header class="matrix-header">
            <div class="matrix-header-left">
              <div class="matrix-title-group">
                <span class="matrix-icon">📚</span>
                <h1 class="matrix-title">KNOWLEDGE MATRIX & QUANT ENCYCLOPEDIA</h1>
              </div>
              <span class="matrix-summary-badge">${this.allNodes.length} ENTITIES · 16 QUANT MODULES · 12 DOMAINS</span>
            </div>
            <button id="matrix-close-btn" class="matrix-close-btn" title="Exit to Canvas (Esc)">
              ✕ Exit to Canvas
            </button>
          </header>

          <div class="matrix-filter-bar">
            <div class="matrix-search-wrapper">
              <span class="matrix-search-icon">🔍</span>
              <input
                type="text"
                id="matrix-search-input"
                class="matrix-search-input"
                placeholder="Search across 109 nodes, formulas, code paths, tags..."
                autocomplete="off"
                spellcheck="false"
              />
            </div>

            <div class="matrix-filter-controls">
              <div class="matrix-filter-group">
                <label for="matrix-domain-filter" class="matrix-filter-label">Domain</label>
                <select id="matrix-domain-filter" class="matrix-filter-select">
                  <option value="all">All Domains (1..12)</option>
                  <option value="1">1. Market Data & Macro Ingestion</option>
                  <option value="2">2. Macro & Regime Classification</option>
                  <option value="3">3. Alpha Mining & Factor Discovery</option>
                  <option value="4">4. Multi-Agent LLM Deliberation</option>
                  <option value="5">5. Causal Inference & DAG Discovery</option>
                  <option value="6">6. Overfit & Deflated Sharpe Gating</option>
                  <option value="7">7. Quantitative Risk Allocation</option>
                  <option value="8">8. Continual Learning & Meta-loop</option>
                  <option value="9">9. Core MT5 Execution Gateway</option>
                  <option value="10">10. Guardian Sentinel & Circuit Breakers</option>
                  <option value="11">11. Memory Persistence & State Store</option>
                  <option value="12">12. Mission Control & Observability</option>
                </select>
              </div>

              <div class="matrix-filter-group">
                <label for="matrix-complexity-filter" class="matrix-filter-label">Complexity</label>
                <select id="matrix-complexity-filter" class="matrix-filter-select">
                  <option value="all">All Complexity Levels</option>
                  <option value="foundational">Foundational (Beginner)</option>
                  <option value="intermediate">Intermediate (Strategy)</option>
                  <option value="advanced">Advanced Quant (PhD/Institutional)</option>
                </select>
              </div>

              <div class="matrix-filter-group">
                <label for="matrix-category-filter" class="matrix-filter-label">Category</label>
                <select id="matrix-category-filter" class="matrix-filter-select">
                  <option value="all">All Categories</option>
                  <option value="quant_theory">Quant Theories</option>
                  <option value="code_module">Code Modules</option>
                  <option value="agent_role">Agent Roles</option>
                  <option value="protective_gate">Protective Gates</option>
                </select>
              </div>

              <div class="matrix-filter-group">
                <label for="matrix-sort-select" class="matrix-filter-label">Sort By</label>
                <select id="matrix-sort-select" class="matrix-filter-select">
                  <option value="name">Name (A-Z)</option>
                  <option value="domain">Domain (1..12)</option>
                  <option value="tier">Tier Level (L0..L3)</option>
                  <option value="callers">Incoming Callers (High to Low)</option>
                  <option value="dependencies">Dependencies (High to Low)</option>
                </select>
              </div>
            </div>
          </div>

          <div id="matrix-grid" class="matrix-grid-wrapper">
            <div id="matrix-card-grid" class="matrix-card-grid"></div>
          </div>
        </div>
      `;

      this._bindEvents(viewEl);
      this._updateSummaryBadge();
      this.renderCards();
    }

    _bindEvents(viewEl) {
      // 1. Close Button
      const closeBtn = viewEl.querySelector('#matrix-close-btn');
      if (closeBtn) {
        closeBtn.addEventListener('click', () => {
          this.closeMatrix();
        });
      }

      // 2. Search Input
      const searchInput = viewEl.querySelector('#matrix-search-input');
      if (searchInput) {
        searchInput.addEventListener('input', () => {
          this.filterEntities({ ...this.currentFilters, search: searchInput.value });
        });
      }

      // 3. Filter Dropdowns
      const domainSelect = viewEl.querySelector('#matrix-domain-filter');
      if (domainSelect) {
        domainSelect.addEventListener('change', () => {
          this.filterEntities({ ...this.currentFilters, domain: domainSelect.value });
        });
      }

      const compSelect = viewEl.querySelector('#matrix-complexity-filter');
      if (compSelect) {
        compSelect.addEventListener('change', () => {
          this.filterEntities({ ...this.currentFilters, complexity: compSelect.value });
        });
      }

      const catSelect = viewEl.querySelector('#matrix-category-filter');
      if (catSelect) {
        catSelect.addEventListener('change', () => {
          this.filterEntities({ ...this.currentFilters, category: catSelect.value });
        });
      }

      const sortSelect = viewEl.querySelector('#matrix-sort-select');
      if (sortSelect) {
        sortSelect.addEventListener('change', () => {
          this.filterEntities({ ...this.currentFilters, sort: sortSelect.value });
        });
      }

      // 4. Card Grid Action Delegation
      const grid = viewEl.querySelector('#matrix-card-grid');
      if (grid) {
        grid.addEventListener('click', (e) => {
          const target = e.target;
          const actionBtn = (target.getAttribute && target.getAttribute('data-action'))
            ? target
            : (target.closest ? target.closest('[data-action]') : null);

          if (actionBtn) {
            const action = actionBtn.getAttribute('data-action');
            const nodeId = actionBtn.getAttribute('data-node-id');
            if (action && nodeId) {
              this.handleAction(action, nodeId);
            }
          }
        });
      }

      // 5. Global Escape Key Listener
      if (typeof window !== 'undefined') {
        window.addEventListener('keydown', (e) => {
          if (e.key === 'Escape' && this.isOpen) {
            this.closeMatrix();
          }
        });
      }
    }
  }

  /**
   * Factory function to instantiate KnowledgeMatrixManager
   */
  function initKnowledgeMatrix(canvasEngine, graphData, deepDiveDrawer = null, container = null) {
    return new KnowledgeMatrixManager(canvasEngine, graphData, deepDiveDrawer, container);
  }

  // Universal Module Export (Browser & CommonJS / Node)
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      KnowledgeMatrixManager,
      initKnowledgeMatrix,
      getNodeComplexity,
      matchesSearchText,
      SUBSYSTEM_INFO,
    };
  }

  if (typeof global !== 'undefined') {
    global.KnowledgeMatrixManager = KnowledgeMatrixManager;
    global.initKnowledgeMatrix = initKnowledgeMatrix;
  }
})(typeof window !== 'undefined' ? window : globalThis);
