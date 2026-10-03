/**
 * Cyber-Quant HyperGraph Studio - 7-Stage Interactive Trade Lifecycle Flight Simulator
 *
 * Provides an automated and interactive guided tour through the 7 execution stages
 * of a live trade event across the trading bot's neural pathways:
 * 1. Market Data & Macro Ingestion (+0.0 ms)
 * 2. Macro & Microstructure Regime Filter (+4.8 ms)
 * 3. Alpha Mining & Factor Discovery (+14.2 ms)
 * 4. Multi-Agent LLM Deliberation (+38.6 ms)
 * 5. Quantitative Risk & Overfit Gating (+62.1 ms)
 * 6. Dual-Velocity MT5 Order Routing (+84.7 ms)
 * 7. Memory Persistence & EWC Meta-Loop (+112.4 ms)
 *
 * Zero external dependencies.
 */

(function (global) {
  'use strict';

  const DEFAULT_LIFECYCLE_STAGES = [
    {
      stage_index: 1,
      name: 'Market Data & Macro Ingestion',
      subtitle: 'Raw Tick Stream & Macro Yield Pulse',
      duration_ms: 2500,
      timestamp: '+0.0 ms',
      active_nodes: [
        'd01_fred_backtest_data',
        'd01_economic_news_terminal',
        'd01_macrofundamental_pipeline',
        'd01_market_data_provider',
        'quant_time_series'
      ],
      active_edges: [
        { source: 'd01_fred_backtest_data', target: 'd01_macrofundamental_pipeline' },
        { source: 'd01_economic_news_terminal', target: 'd02_news_trading_gate' }
      ],
      narrative: 'Real-time tick data streams from the MT5 broker gateway concurrently with macroeconomic releases from FRED. Ticks are checked for price gap anomalies, while alternative news sentiment is vectorized into real-time market tensors.'
    },
    {
      stage_index: 2,
      name: 'Macro & Microstructure Regime Filter',
      subtitle: 'HMM Volatility State & News Blackout Gating',
      duration_ms: 2500,
      timestamp: '+4.8 ms',
      active_nodes: [
        'd02_regime_detector',
        'd02_spmc_regime_filter',
        'd02_regime_duration_tracker',
        'd02_news_trading_gate',
        'quant_regime_detection',
        'quant_volatility'
      ],
      active_edges: [
        { source: 'd02_regime_detector', target: 'd03_alpha_mining_pipeline' },
        { source: 'd02_spmc_regime_filter', target: 'd07_risk_manager' }
      ],
      narrative: 'The Bayesian Online Changepoint Detector and Hidden Markov Models analyze current volatility clustering. If high-impact news releases or flash spreads are detected, the News Trading Gate throttles new order entry.'
    },
    {
      stage_index: 3,
      name: 'Alpha Mining & Factor Discovery',
      subtitle: 'Genetic Formula Alphas & Orthogonalization',
      duration_ms: 3000,
      timestamp: '+14.2 ms',
      active_nodes: [
        'd03_alpha_mining_pipeline',
        'd03_alpha_factors',
        'd03_orthogonalize_alphas',
        'd03_combine_alphas',
        'quant_pca_factors',
        'quant_asset_pricing'
      ],
      active_edges: [
        { source: 'd03_alpha_factors', target: 'd03_orthogonalize_alphas' },
        { source: 'd03_orthogonalize_alphas', target: 'd03_combine_alphas' },
        { source: 'd03_combine_alphas', target: 'd04_llm_orchestrator' }
      ],
      narrative: 'Active alpha signals are computed using genetic programming factors. Signals undergo Gram-Schmidt orthogonalization to strip out multi-collinearity, producing a pure, non-redundant ensemble alpha score.'
    },
    {
      stage_index: 4,
      name: 'Multi-Agent LLM Deliberation',
      subtitle: 'Decentralized Hive Debate (Proposer, Critic, Risk)',
      duration_ms: 3500,
      timestamp: '+38.6 ms',
      active_nodes: [
        'd04_llm_orchestrator',
        'd04_grpo_proposer',
        'd04_agent_critic',
        'd04_llm_veto_operator',
        'd04_agent_event_bus',
        'quant_llm_finance'
      ],
      active_edges: [
        { source: 'd04_llm_orchestrator', target: 'd04_grpo_proposer' },
        { source: 'd04_grpo_proposer', target: 'd04_agent_critic' },
        { source: 'd04_agent_critic', target: 'd04_llm_veto_operator' }
      ],
      narrative: 'The Autonomous Multi-Agent Hive initiates structured deliberation. The GRPO Proposer drafts trade candidates with thesis and confidence; the Market Critic scrutinizes counter-evidence; and the LLM Veto Operator filters hallucinations.'
    },
    {
      stage_index: 5,
      name: 'Quantitative Risk & Overfit Gating',
      subtitle: 'Deflated Sharpe (DSR) & Fractional Kelly Sizing',
      duration_ms: 3000,
      timestamp: '+62.1 ms',
      active_nodes: [
        'd06_deflated_sharpe_test',
        'd06_overfit_gate',
        'd07_risk_manager',
        'd07_trading_constraint_engine',
        'd07_correlation_risk',
        'quant_deflated_sharpe',
        'quant_drawdown_management'
      ],
      active_edges: [
        { source: 'd06_deflated_sharpe_test', target: 'd06_overfit_gate' },
        { source: 'd06_overfit_gate', target: 'd07_risk_manager' },
        { source: 'd07_risk_manager', target: 'd09_core_order_execution' }
      ],
      narrative: 'The candidate trade passes through the statistical Overfit Gate where Deflated Sharpe Ratio confirms validity. The Risk Manager calculates fractional Kelly lot sizing while verifying correlation and margin ceilings.'
    },
    {
      stage_index: 6,
      name: 'Dual-Velocity MT5 Order Routing',
      subtitle: 'Optimal Execution Scheduling (TWAP/VWAP) & Broker Fill',
      duration_ms: 3000,
      timestamp: '+84.7 ms',
      active_nodes: [
        'd09_core_order_execution',
        'd09_optimal_execution_scheduler',
        'd09_mt5_trader',
        'd09_transaction_cost_engine',
        'd10_circuit_breaker_gate'
      ],
      active_edges: [
        { source: 'd09_core_order_execution', target: 'd09_optimal_execution_scheduler' },
        { source: 'd09_optimal_execution_scheduler', target: 'd09_mt5_trader' }
      ],
      narrative: 'Authorized trade lots are sliced by the Optimal Execution Scheduler using adaptive TWAP/VWAP trajectories to minimize slippage. Direct memory-mapped socket requests execute orders with broker-side hard stops attached.'
    },
    {
      stage_index: 7,
      name: 'Memory Persistence & EWC Meta-Loop',
      subtitle: 'ACID Journal & Elastic Weight Consolidation',
      duration_ms: 2500,
      timestamp: '+112.4 ms',
      active_nodes: [
        'd11_trading_memory',
        'd11_store_backed_state',
        'd08_ewc_forgetting_prevention',
        'd08_meta_adaptive_loop',
        'd12_compliance_dashboard',
        'quant_transfer_learning'
      ],
      active_edges: [
        { source: 'd09_mt5_trader', target: 'd11_trading_memory' },
        { source: 'd11_trading_memory', target: 'd08_meta_adaptive_loop' },
        { source: 'd08_meta_adaptive_loop', target: 'd08_ewc_forgetting_prevention' }
      ],
      narrative: 'Execution fills, slippage, and timestamps are committed to ACID SQLite journal. The EWC engine updates the diagonal Fisher Information Matrix to retain learned patterns across regime shifts without catastrophic forgetting.'
    }
  ];

  /**
   * LifecycleTourManager coordinates camera movement, node illumination, edge pulses,
   * narrative card updates, and playback timers for the 7 trade lifecycle stages.
   */
  class LifecycleTourManager {
    /**
     * @param {Object} canvasEngine CanvasEngine instance
     * @param {Object} graphData Initial payload containing lifecycle_stages and nodes
     * @param {HTMLElement} [container=document.body] Root container element
     */
    constructor(canvasEngine, graphData, container = null) {
      this.engine = canvasEngine;
      this.data = graphData || (canvasEngine && canvasEngine.data) || {};
      this.container = container || (typeof document !== 'undefined' ? document.body : null);

      // Load stages from payload or use default fallback
      const rawStages = (this.data.lifecycle_stages && this.data.lifecycle_stages.length === 7)
        ? this.data.lifecycle_stages
        : DEFAULT_LIFECYCLE_STAGES;

      this.stages = rawStages.map((s, idx) => {
        const fallback = DEFAULT_LIFECYCLE_STAGES[idx] || {};
        return {
          stage_index: s.stage_index || idx + 1,
          name: s.name || fallback.name || `Stage ${idx + 1}`,
          subtitle: s.subtitle || fallback.subtitle || '',
          duration_ms: typeof s.duration_ms === 'number' ? s.duration_ms : (fallback.duration_ms || 3000),
          timestamp: s.timestamp || fallback.timestamp || `+${(idx * 18.5).toFixed(1)} ms`,
          active_nodes: Array.isArray(s.active_nodes) ? [...s.active_nodes] : (fallback.active_nodes || []),
          active_edges: Array.isArray(s.active_edges) ? [...s.active_edges] : (fallback.active_edges || []),
          narrative: s.narrative || fallback.narrative || 'Executing algorithmic trade lifecycle step.'
        };
      });

      // Playback state
      this.currentStageIndex = 0;
      this.isPlaying = false;
      this.isActive = false;
      this.speed = 1.0;
      this.timerId = null;

      // Event callbacks
      this.onStageChange = null;
      this.onTourExit = null;
      this.onPlayStateChange = null;

      this._init();
    }

    _init() {
      if (!this.container || typeof document === 'undefined') return;

      this._mountControlsBar();
      this._mountNarrativeCard();
    }

    // --- DOM Mounting ---

    _mountControlsBar() {
      let controlsBar = this.container.querySelector('#tour-controls-bar');
      if (!controlsBar) {
        controlsBar = document.createElement('div');
        controlsBar.id = 'tour-controls-bar';
        controlsBar.className = 'tour-controls-bar hidden';
        controlsBar.style.display = 'none';
        this.container.appendChild(controlsBar);
      }

      controlsBar.innerHTML = `
        <div class="tour-controls-inner">
          <div class="tour-nav-group">
            <button class="tour-btn tour-step-btn" id="tour-prev-btn" title="Previous Stage (←)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="19 20 9 12 19 4 19 20"></polygon><line x1="5" y1="19" x2="5" y2="5"></line></svg>
            </button>
            <button class="tour-btn tour-play-btn" id="tour-play-pause-btn" title="Play / Pause (Space)">
              <span class="play-icon">▶</span>
            </button>
            <button class="tour-btn tour-step-btn" id="tour-next-btn" title="Next Stage (→)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polygon points="5 4 15 12 5 20 5 4"></polygon><line x1="19" y1="5" x2="19" y2="19"></line></svg>
            </button>
          </div>

          <div class="tour-stage-dots">
            ${this.stages.map((st, i) => `
              <button class="tour-stage-dot ${i === 0 ? 'active' : ''}" data-stage="${i}" title="${st.stage_index}. ${st.name}">
                <span class="dot-num">0${i + 1}</span>
              </button>
            `).join('')}
          </div>

          <div class="tour-speed-group">
            <select class="tour-speed-select" id="tour-speed-selector" title="Playback Speed">
              <option value="0.5">0.5x</option>
              <option value="1" selected>1.0x</option>
              <option value="2">2.0x</option>
            </select>
          </div>

          <div class="tour-exit-group">
            <button class="tour-btn tour-exit-btn" id="tour-exit-btn" title="Exit Tour (Esc)">
              <span>✕</span>
            </button>
          </div>
        </div>
      `;

      // Event bindings
      const prevBtn = controlsBar.querySelector('#tour-prev-btn');
      const playBtn = controlsBar.querySelector('#tour-play-pause-btn');
      const nextBtn = controlsBar.querySelector('#tour-next-btn');
      const speedSelect = controlsBar.querySelector('#tour-speed-selector');
      const exitBtn = controlsBar.querySelector('#tour-exit-btn');
      const dots = controlsBar.querySelectorAll('.tour-stage-dot');

      if (prevBtn) prevBtn.addEventListener('click', () => this.prevStage());
      if (playBtn) playBtn.addEventListener('click', () => this.togglePlayPause());
      if (nextBtn) nextBtn.addEventListener('click', () => this.nextStage());
      if (speedSelect) {
        speedSelect.addEventListener('change', (e) => {
          const val = parseFloat(e.target.value);
          if (!isNaN(val)) this.setSpeed(val);
        });
      }
      if (exitBtn) exitBtn.addEventListener('click', () => this.stopTour());

      dots.forEach(dot => {
        dot.addEventListener('click', () => {
          const stageIdx = parseInt(dot.getAttribute('data-stage'), 10);
          if (!isNaN(stageIdx)) this.goToStage(stageIdx);
        });
      });
    }

    _mountNarrativeCard() {
      let card = this.container.querySelector('#tour-narrative-card');
      if (!card) {
        card = document.createElement('div');
        card.id = 'tour-narrative-card';
        card.className = 'tour-narrative-card hidden';
        card.style.display = 'none';
        this.container.appendChild(card);
      }

      this._renderNarrativeCardContent(card);
    }

    _renderNarrativeCardContent(card) {
      if (!card) return;
      const stage = this.stages[this.currentStageIndex] || this.stages[0];

      // Retrieve human-friendly node chips
      const chipsHtml = (stage.active_nodes || []).map(nodeId => {
        const node = this._resolveNode(nodeId);
        const name = node ? (node.name || nodeId) : nodeId;
        return `<span class="tour-chip" data-node-id="${nodeId}" title="Focus ${name}">${name}</span>`;
      }).join('');

      card.innerHTML = `
        <div class="tour-card-header">
          <div class="tour-stage-badge">
            <span class="badge-prefix">STAGE 0${stage.stage_index} / 0${this.stages.length}</span>
            <span class="badge-separator">·</span>
            <span class="badge-title">${stage.name.toUpperCase()}</span>
          </div>
          <div class="tour-timestamp">
            <span class="pulse-clock-dot"></span>
            <span>${stage.timestamp || '+0.0 ms'}</span>
          </div>
        </div>
        ${stage.subtitle ? `<div class="tour-subtitle">${stage.subtitle}</div>` : ''}
        <div class="tour-narrative-text">
          ${stage.narrative}
        </div>
        <div class="tour-active-chips-label">ACTIVE SUB-SYSTEM COMPONENTS (${(stage.active_nodes || []).length})</div>
        <div class="tour-active-chips">
          ${chipsHtml}
        </div>
      `;

      // Allow clicking individual chips to focus that node in canvas
      card.querySelectorAll('.tour-chip').forEach(chip => {
        chip.addEventListener('click', (e) => {
          const nId = chip.getAttribute('data-node-id');
          if (nId && this.engine && typeof this.engine.focusNode === 'function') {
            this.engine.focusNode(nId, 1.4);
          }
        });
      });
    }

    _resolveNode(nodeId) {
      if (this.engine && this.engine.nodeMap && this.engine.nodeMap.has(nodeId)) {
        return this.engine.nodeMap.get(nodeId);
      }
      if (Array.isArray(this.data.nodes)) {
        return this.data.nodes.find(n => n.id === nodeId);
      }
      return null;
    }

    // --- Tour Control Flow ---

    startTour() {
      this.isActive = true;
      this.isPlaying = true;

      // Show DOM UI
      const controlsBar = this.container ? this.container.querySelector('#tour-controls-bar') : null;
      const narrativeCard = this.container ? this.container.querySelector('#tour-narrative-card') : null;

      if (controlsBar) {
        controlsBar.classList.remove('hidden');
        controlsBar.style.display = 'flex';
      }
      if (narrativeCard) {
        narrativeCard.classList.remove('hidden');
        narrativeCard.style.display = 'block';
      }

      this.goToStage(this.currentStageIndex || 0);
      this._updatePlayButtonUI();

      if (this.onPlayStateChange) {
        this.onPlayStateChange(true);
      }
    }

    stopTour() {
      const wasActive = this.isActive;
      this.isActive = false;
      this.isPlaying = false;
      this._clearTimer();

      // Hide DOM UI
      const controlsBar = this.container ? this.container.querySelector('#tour-controls-bar') : null;
      const narrativeCard = this.container ? this.container.querySelector('#tour-narrative-card') : null;

      if (controlsBar) {
        controlsBar.classList.add('hidden');
        controlsBar.style.display = 'none';
      }
      if (narrativeCard) {
        narrativeCard.classList.add('hidden');
        narrativeCard.style.display = 'none';
      }

      // Clear engine stage highlights
      if (this.engine) {
        this.engine.activeStageIndex = null;
        if (this.engine.activeStageNodeIds && typeof this.engine.activeStageNodeIds.clear === 'function') {
          this.engine.activeStageNodeIds.clear();
        } else {
          this.engine.activeStageNodeIds = new Set();
        }
        this.engine.activeStageEdges = [];
        if (typeof this.engine.resetCamera === 'function') {
          this.engine.resetCamera();
        }
      }

      this._updatePlayButtonUI();

      if (wasActive && this.onTourExit) {
        this.onTourExit();
      }
      if (wasActive && this.onPlayStateChange) {
        this.onPlayStateChange(false);
      }
    }

    togglePlayPause() {
      if (!this.isActive) {
        this.startTour();
        return;
      }
      if (this.isPlaying) {
        this.pause();
      } else {
        this.play();
      }
    }

    play() {
      this.isPlaying = true;
      this._scheduleNextStageTimer();
      this._updatePlayButtonUI();
      if (this.onPlayStateChange) {
        this.onPlayStateChange(true);
      }
    }

    pause() {
      this.isPlaying = false;
      this._clearTimer();
      this._updatePlayButtonUI();
      if (this.onPlayStateChange) {
        this.onPlayStateChange(false);
      }
    }

    nextStage() {
      const nextIdx = (this.currentStageIndex + 1) % this.stages.length;
      this.goToStage(nextIdx);
    }

    prevStage() {
      const prevIdx = (this.currentStageIndex - 1 + this.stages.length) % this.stages.length;
      this.goToStage(prevIdx);
    }

    goToStage(stageIdx) {
      if (stageIdx < 0 || stageIdx >= this.stages.length) return;
      this.currentStageIndex = stageIdx;
      const stage = this.stages[stageIdx];

      // 1. Highlight Active Nodes and Edges in Engine
      if (this.engine) {
        this.engine.activeStageIndex = stageIdx;
        this.engine.activeStageNodeIds = new Set(stage.active_nodes || []);
        this.engine.activeStageEdges = stage.active_edges ? [...stage.active_edges] : [];

        // Stimulate pulse speeds on active edges
        if (Array.isArray(this.engine.edges)) {
          for (const edge of this.engine.edges) {
            const isActive = (stage.active_edges || []).some(
              ae => ae.source === edge.source && ae.target === edge.target
            );
            if (isActive) {
              edge.pulseSpeed = 0.024; // 3x speed stimulus
            } else {
              edge.pulseSpeed = 0.008;
            }
          }
        }

        // 2. Camera Glide: Center and frame active stage nodes
        this._glideCameraToStageNodes(stage.active_nodes);
      }

      // 3. Update DOM UI
      this._updateStageDotsUI();
      const narrativeCard = this.container ? this.container.querySelector('#tour-narrative-card') : null;
      if (narrativeCard) {
        this._renderNarrativeCardContent(narrativeCard);
      }

      // 4. Timer scheduling if playing
      if (this.isPlaying) {
        this._scheduleNextStageTimer();
      }

      // 5. Notify callback
      if (this.onStageChange) {
        this.onStageChange(stageIdx, stage);
      }
    }

    setSpeed(speedMultiplier) {
      this.speed = Math.max(0.25, Math.min(5.0, speedMultiplier));
      const speedSelect = this.container ? this.container.querySelector('#tour-speed-selector') : null;
      if (speedSelect) {
        speedSelect.value = String(this.speed);
      }
      if (this.isPlaying) {
        this._scheduleNextStageTimer();
      }
    }

    // --- Internal Helpers ---

    _glideCameraToStageNodes(nodeIds) {
      if (!this.engine || !Array.isArray(nodeIds) || nodeIds.length === 0) return;
      if (typeof this.engine.glideTo !== 'function') return;

      let sumX = 0;
      let sumY = 0;
      let count = 0;
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;

      for (const id of nodeIds) {
        const node = this._resolveNode(id);
        if (node && typeof node.x === 'number' && typeof node.y === 'number') {
          sumX += node.x;
          sumY += node.y;
          count++;
          if (node.x < minX) minX = node.x;
          if (node.x > maxX) maxX = node.x;
          if (node.y < minY) minY = node.y;
          if (node.y > maxY) maxY = node.y;
        }
      }

      if (count > 0) {
        const centerX = sumX / count;
        const centerY = sumY / count;
        const spanX = maxX - minX;
        const spanY = maxY - minY;
        const maxSpan = Math.max(spanX, spanY, 300);

        // Optimal zoom dynamically fitted to span
        let targetZoom = 1.25;
        if (maxSpan > 1200) targetZoom = 0.65;
        else if (maxSpan > 800) targetZoom = 0.85;
        else if (maxSpan > 500) targetZoom = 1.05;

        this.engine.glideTo(centerX, centerY, targetZoom);
      }
    }

    _scheduleNextStageTimer() {
      this._clearTimer();
      if (!this.isPlaying) return;

      const currentStage = this.stages[this.currentStageIndex];
      const baseDuration = (currentStage && currentStage.duration_ms) || 3000;
      const effectiveDuration = Math.max(400, baseDuration / this.speed);

      this.timerId = setTimeout(() => {
        if (this.isPlaying && this.isActive) {
          this.nextStage();
        }
      }, effectiveDuration);
    }

    _clearTimer() {
      if (this.timerId !== null) {
        clearTimeout(this.timerId);
        this.timerId = null;
      }
    }

    _updatePlayButtonUI() {
      const playBtn = this.container ? this.container.querySelector('#tour-play-pause-btn') : null;
      if (!playBtn) return;

      if (this.isPlaying) {
        playBtn.innerHTML = '<span class="pause-icon">⏸</span>';
        playBtn.setAttribute('title', 'Pause Tour (Space)');
        playBtn.classList.add('playing');
      } else {
        playBtn.innerHTML = '<span class="play-icon">▶</span>';
        playBtn.setAttribute('title', 'Play Tour (Space)');
        playBtn.classList.remove('playing');
      }
    }

    _updateStageDotsUI() {
      const dots = this.container ? this.container.querySelectorAll('.tour-stage-dot') : [];
      dots.forEach((dot, idx) => {
        if (idx === this.currentStageIndex) {
          dot.classList.add('active');
        } else {
          dot.classList.remove('active');
        }
      });
    }
  }

  /**
   * Factory function to instantiate LifecycleTourManager
   */
  function initLifecycleTour(canvasEngine, graphData, container = null) {
    return new LifecycleTourManager(canvasEngine, graphData, container);
  }

  // Universal Module Export (Browser & CommonJS / Node)
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      LifecycleTourManager,
      initLifecycleTour,
      DEFAULT_LIFECYCLE_STAGES
    };
  }

  if (typeof global !== 'undefined') {
    global.LifecycleTourManager = LifecycleTourManager;
    global.initLifecycleTour = initLifecycleTour;
    global.DEFAULT_LIFECYCLE_STAGES = DEFAULT_LIFECYCLE_STAGES;
  }
})(typeof window !== 'undefined' ? window : globalThis);
