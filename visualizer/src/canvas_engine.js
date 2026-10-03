/**
 * Cyber-Quant HyperGraph Studio - Canvas 2D Rendering Engine
 *
 * High-DPI hardware-accelerated Canvas 2D engine with spring-damped camera physics,
 * ambient coordinate grid, 5 topological layouts, animated Bezier edge signal pulses,
 * collision-tested rounded pills, and interactive minimap radar HUD.
 *
 * Zero external dependencies.
 */

(function (global) {
  'use strict';

  // Subsystem 1..12 default cyber-quant palette
  const SUBSYSTEM_COLORS = {
    1: '#38bdf8',  // Market Data & Macro Ingestion (Sky Blue)
    2: '#818cf8',  // Macro & Regime Classification (Indigo)
    3: '#34d399',  // Alpha Mining & Factor Discovery (Emerald)
    4: '#c084fc',  // Multi-Agent LLM Deliberation (Purple)
    5: '#f472b6',  // Causal Inference & DAG Discovery (Pink)
    6: '#fb923c',  // Overfit & Deflated Sharpe Gating (Orange)
    7: '#f87171',  // Quantitative Risk Allocation (Rose)
    8: '#a78bfa',  // Continual Learning & Meta-loop (Violet)
    9: '#4ade80',  // Core MT5 Execution Gateway (Green)
    10: '#facc15', // Guardian Sentinel & Circuit Breakers (Amber)
    11: '#2dd4bf', // Memory Persistence & State Store (Teal)
    12: '#60a5fa', // Mission Control & Observability (Blue)
  };

  const DEFAULT_COLOR = '#94a3b8';

  class CanvasEngine {
    /**
     * @param {Object} options Configuration object
     * @param {HTMLCanvasElement} [options.canvas] Main viewport canvas
     * @param {HTMLCanvasElement} [options.radarCanvas] Minimap radar canvas
     * @param {Object} [options.data] Initial graph payload
     * @param {Function} [options.onNodeSelect] Callback when a node is selected
     * @param {Function} [options.onNodeHover] Callback when a node is hovered
     * @param {Function} [options.onCameraMove] Callback on camera movement
     */
    constructor(options = {}) {
      this.canvas = options.canvas || null;
      this.ctx = null;
      this.radarCanvas = options.radarCanvas || null;
      this.radarCtx = null;

      // Screen dimensions & DPI
      this.width = 1280;
      this.height = 720;
      this.dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
      this.radarWidth = 240;
      this.radarHeight = 160;

      // Camera Physics
      this.cameraX = 0;
      this.cameraY = 0;
      this.targetCameraX = 0;
      this.targetCameraY = 0;
      this.zoom = 0.85;
      this.targetZoom = 0.85;
      this.minZoom = 0.1;
      this.maxZoom = 4.0;
      this.lerpFactor = 0.12;
      this.isDragging = false;
      this.lastMouseX = 0;
      this.lastMouseY = 0;
      this.dragMoved = false;

      // Graph Data & Indices
      this.nodes = [];
      this.edges = [];
      this.subsystems = [];
      this.lifecycleStages = [];
      this.nodeMap = new Map();
      this.callersMap = new Map();
      this.dependenciesMap = new Map();

      // Layout System
      this.currentLayout = 'islands'; // 'islands' | 'pipeline' | 'radar' | 'tree' | 'force'
      this.targetLayout = 'islands';
      this.isTransitioning = false;
      this.transitionProgress = 1.0;
      this.transitionDuration = 600; // ms
      this.transitionStartTime = 0;
      this.startPositions = new Map();
      this.targetPositions = new Map();

      // Force Layout Physics
      this.forceRunning = false;
      this.forceVelocities = new Map(); // id -> { vx, vy }

      // Interactivity & Highlighting
      this.selectedNodeId = null;
      this.hoveredNodeId = null;
      this.activeStageIndex = null;
      this.activeStageNodeIds = new Set();
      this.activeStageEdges = [];
      this.filterSubsystemId = null;
      this.hiddenSubsystems = new Set();
      this.searchQuery = '';

      // Animation & Edge Pulses
      this.isRunning = false;
      this.animFrameId = null;
      this.lastTimestamp = 0;
      this.globalPulsePhase = 0;
      this.particles = [];
      this._initParticles(50);

      // Callbacks
      this.onNodeSelect = options.onNodeSelect || null;
      this.onNodeHover = options.onNodeHover || null;
      this.onCameraMove = options.onCameraMove || null;

      // Event listener references for cleanup
      this._boundOnResize = this._onResize.bind(this);
      this._boundOnMouseDown = this._onMouseDown.bind(this);
      this._boundOnMouseMove = this._onMouseMove.bind(this);
      this._boundOnMouseUp = this._onMouseUp.bind(this);
      this._boundOnWheel = this._onWheel.bind(this);
      this._boundOnRadarClick = this._onRadarClick.bind(this);

      if (options.data) {
        this.setData(options.data);
      } else if (typeof window !== 'undefined' && window.GRAPH_DATA) {
        this.setData(window.GRAPH_DATA);
      }

      if (this.canvas) {
        this.init(this.canvas, this.radarCanvas);
      }
    }

    /**
     * Initialize engine with DOM canvas elements and bind interaction listeners.
     */
    init(canvas, radarCanvas = null) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.radarCanvas = radarCanvas;
      if (radarCanvas) {
        this.radarCtx = radarCanvas.getContext('2d');
      }

      this._updateDimensions();
      this._bindEvents();
      this.start();
      return this;
    }

    /**
     * Set or replace graph data payload.
     */
    setData(payload) {
      if (!payload) return;
      this.nodes = Array.isArray(payload.nodes) ? payload.nodes.map(n => ({ ...n })) : [];
      this.edges = Array.isArray(payload.edges) ? payload.edges.map(e => ({ ...e })) : [];
      this.subsystems = Array.isArray(payload.subsystems) ? payload.subsystems : [];
      this.lifecycleStages = Array.isArray(payload.lifecycle_stages) ? payload.lifecycle_stages : [];

      this.nodeMap.clear();
      this.callersMap.clear();
      this.dependenciesMap.clear();

      for (const node of this.nodes) {
        this.nodeMap.set(node.id, node);
        // Ensure initial coordinates
        if (typeof node.x !== 'number') node.x = node.x_island || 0;
        if (typeof node.y !== 'number') node.y = node.y_island || 0;
        this.callersMap.set(node.id, new Set(node.callers || []));
        this.dependenciesMap.set(node.id, new Set(node.dependencies || []));
      }

      // Sync edges
      for (const edge of this.edges) {
        if (!this.callersMap.has(edge.target)) this.callersMap.set(edge.target, new Set());
        if (!this.dependenciesMap.has(edge.source)) this.dependenciesMap.set(edge.source, new Set());
        this.callersMap.get(edge.target).add(edge.source);
        this.dependenciesMap.get(edge.source).add(edge.target);

        // Edge pulse progression state
        if (typeof edge.pulseProgress !== 'number') {
          edge.pulseProgress = Math.random();
        }
        if (typeof edge.pulseSpeed !== 'number') {
          edge.pulseSpeed = 0.008 + Math.random() * 0.006;
        }
      }

      this.applyLayout(this.currentLayout, false);
      this.resetCamera();
    }

    /**
     * Adjust canvas backing store to match display pixel ratio.
     */
    setDimensions(width, height) {
      this.width = width;
      this.height = height;
      this.dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;

      if (this.canvas && this.ctx) {
        this.canvas.width = Math.floor(width * this.dpr);
        this.canvas.height = Math.floor(height * this.dpr);
        this.canvas.style.width = `${width}px`;
        this.canvas.style.height = `${height}px`;
        this.ctx.setTransform(1, 0, 0, 1, 0, 0);
        this.ctx.scale(this.dpr, this.dpr);
      }

      if (this.radarCanvas && this.radarCtx) {
        const rw = this.radarWidth;
        const rh = this.radarHeight;
        this.radarCanvas.width = Math.floor(rw * this.dpr);
        this.radarCanvas.height = Math.floor(rh * this.dpr);
        this.radarCanvas.style.width = `${rw}px`;
        this.radarCanvas.style.height = `${rh}px`;
        this.radarCtx.setTransform(1, 0, 0, 1, 0, 0);
        this.radarCtx.scale(this.dpr, this.dpr);
      }
    }

    _updateDimensions() {
      if (typeof window !== 'undefined' && this.canvas) {
        const rect = this.canvas.getBoundingClientRect();
        const w = rect.width || window.innerWidth || 1280;
        const h = rect.height || window.innerHeight || 720;
        this.setDimensions(w, h);
      }
    }

    _bindEvents() {
      if (typeof window === 'undefined' || !this.canvas) return;

      window.addEventListener('resize', this._boundOnResize);
      this.canvas.addEventListener('mousedown', this._boundOnMouseDown);
      window.addEventListener('mousemove', this._boundOnMouseMove);
      window.addEventListener('mouseup', this._boundOnMouseUp);
      this.canvas.addEventListener('wheel', this._boundOnWheel, { passive: false });

      if (this.radarCanvas) {
        this.radarCanvas.addEventListener('click', this._boundOnRadarClick);
      }
    }

    _unbindEvents() {
      if (typeof window === 'undefined') return;
      window.removeEventListener('resize', this._boundOnResize);
      if (this.canvas) {
        this.canvas.removeEventListener('mousedown', this._boundOnMouseDown);
        this.canvas.removeEventListener('wheel', this._boundOnWheel);
      }
      window.removeEventListener('mousemove', this._boundOnMouseMove);
      window.removeEventListener('mouseup', this._boundOnMouseUp);
      if (this.radarCanvas) {
        this.radarCanvas.removeEventListener('click', this._boundOnRadarClick);
      }
    }

    _onResize() {
      this._updateDimensions();
    }

    _onMouseDown(e) {
      if (e.button !== 0 && e.button !== 1) return;
      this.isDragging = true;
      this.dragMoved = false;
      this.lastMouseX = e.clientX;
      this.lastMouseY = e.clientY;
    }

    _onMouseMove(e) {
      if (this.isDragging) {
        const dx = e.clientX - this.lastMouseX;
        const dy = e.clientY - this.lastMouseY;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
          this.dragMoved = true;
        }
        this.cameraX -= dx / this.zoom;
        this.cameraY -= dy / this.zoom;
        this.targetCameraX = this.cameraX;
        this.targetCameraY = this.cameraY;
        this.lastMouseX = e.clientX;
        this.lastMouseY = e.clientY;
        if (this.onCameraMove) this.onCameraMove(this.cameraX, this.cameraY, this.zoom);
        return;
      }

      // Hit testing for hover
      if (!this.canvas) return;
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const { x: wx, y: wy } = this.screenToWorld(sx, sy);

      const hitNode = this.getNodeAtWorld(wx, wy);
      const newHoverId = hitNode ? hitNode.id : null;
      if (newHoverId !== this.hoveredNodeId) {
        this.hoveredNodeId = newHoverId;
        if (this.canvas) {
          this.canvas.style.cursor = hitNode ? 'pointer' : 'default';
        }
        if (this.onNodeHover) {
          this.onNodeHover(hitNode);
        }
      }
    }

    _onMouseUp(e) {
      if (this.isDragging) {
        this.isDragging = false;
        if (!this.dragMoved) {
          // It was a click!
          const rect = this.canvas.getBoundingClientRect();
          const sx = e.clientX - rect.left;
          const sy = e.clientY - rect.top;
          const { x: wx, y: wy } = this.screenToWorld(sx, sy);
          const hitNode = this.getNodeAtWorld(wx, wy);
          this.selectNode(hitNode ? hitNode.id : null);
        }
      }
    }

    _onWheel(e) {
      e.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const cursorSx = e.clientX - rect.left;
      const cursorSy = e.clientY - rect.top;

      const zoomDelta = e.deltaY < 0 ? 1.15 : 0.85;
      this.zoomAtScreen(cursorSx, cursorSy, zoomDelta);
    }

    _onRadarClick(e) {
      if (!this.radarCanvas) return;
      const rect = this.radarCanvas.getBoundingClientRect();
      const rx = e.clientX - rect.left;
      const ry = e.clientY - rect.top;
      const { x: wx, y: wy } = this.minimapToWorld(rx, ry);
      this.glideTo(wx, wy);
    }

    /**
     * Zoom focused on a specific screen point so the world point under cursor remains invariant.
     */
    zoomAtScreen(screenX, screenY, factor) {
      const worldBefore = this.screenToWorld(screenX, screenY);
      const nextZoom = Math.max(this.minZoom, Math.min(this.maxZoom, this.targetZoom * factor));
      this.targetZoom = nextZoom;
      // Instant adjustment for camera target so invariant point holds
      this.targetCameraX = worldBefore.x - (screenX - this.width / 2) / nextZoom;
      this.targetCameraY = worldBefore.y - (screenY - this.height / 2) / nextZoom;
    }

    // --- Coordinate Transforms ---

    worldToScreen(wx, wy) {
      const sx = (wx - this.cameraX) * this.zoom + this.width / 2;
      const sy = (wy - this.cameraY) * this.zoom + this.height / 2;
      return { x: sx, y: sy };
    }

    screenToWorld(sx, sy) {
      const wx = (sx - this.width / 2) / this.zoom + this.cameraX;
      const wy = (sy - this.height / 2) / this.zoom + this.cameraY;
      return { x: wx, y: wy };
    }

    getWorldBounds() {
      if (this.nodes.length === 0) {
        return { minX: -1000, maxX: 1000, minY: -1000, maxY: 1000, width: 2000, height: 2000 };
      }
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const n of this.nodes) {
        if (n.x < minX) minX = n.x;
        if (n.x > maxX) maxX = n.x;
        if (n.y < minY) minY = n.y;
        if (n.y > maxY) maxY = n.y;
      }
      const padding = 300;
      minX -= padding;
      maxX += padding;
      minY -= padding;
      maxY += padding;
      return { minX, maxX, minY, maxY, width: maxX - minX, height: maxY - minY };
    }

    worldToMinimap(wx, wy) {
      const bounds = this.getWorldBounds();
      const scaleX = this.radarWidth / bounds.width;
      const scaleY = this.radarHeight / bounds.height;
      const scale = Math.min(scaleX, scaleY) * 0.9;
      const midX = (bounds.minX + bounds.maxX) / 2;
      const midY = (bounds.minY + bounds.maxY) / 2;

      const mx = this.radarWidth / 2 + (wx - midX) * scale;
      const my = this.radarHeight / 2 + (wy - midY) * scale;
      return { x: mx, y: my, scale };
    }

    minimapToWorld(mx, my) {
      const bounds = this.getWorldBounds();
      const scaleX = this.radarWidth / bounds.width;
      const scaleY = this.radarHeight / bounds.height;
      const scale = Math.min(scaleX, scaleY) * 0.9;
      const midX = (bounds.minX + bounds.maxX) / 2;
      const midY = (bounds.minY + bounds.maxY) / 2;

      const wx = (mx - this.radarWidth / 2) / scale + midX;
      const wy = (my - this.radarHeight / 2) / scale + midY;
      return { x: wx, y: wy };
    }

    // --- Selection & Camera Movement ---

    selectNode(nodeId) {
      this.selectedNodeId = nodeId;
      const node = nodeId ? this.nodeMap.get(nodeId) : null;
      if (node) {
        this.focusNode(nodeId);
      }
      if (this.currentLayout === 'tree') {
        this.computeLayout('tree');
      }
      if (this.onNodeSelect) {
        this.onNodeSelect(node);
      }
    }

    focusNode(nodeId, desiredZoom = 1.3) {
      const node = this.nodeMap.get(nodeId);
      if (!node) return;
      this.glideTo(node.x, node.y, desiredZoom);
    }

    glideTo(worldX, worldY, desiredZoom = null) {
      this.targetCameraX = worldX;
      this.targetCameraY = worldY;
      if (typeof desiredZoom === 'number') {
        this.targetZoom = Math.max(this.minZoom, Math.min(this.maxZoom, desiredZoom));
      }
    }

    resetCamera() {
      const bounds = this.getWorldBounds();
      this.targetCameraX = (bounds.minX + bounds.maxX) / 2;
      this.targetCameraY = (bounds.minY + bounds.maxY) / 2;
      const fitZoomX = (this.width * 0.8) / bounds.width;
      const fitZoomY = (this.height * 0.8) / bounds.height;
      this.targetZoom = Math.max(this.minZoom, Math.min(1.2, Math.min(fitZoomX, fitZoomY)));
    }

    // --- 5 Topological Layouts ---

    setLayout(layoutName) {
      if (!['islands', 'pipeline', 'radar', 'tree', 'force'].includes(layoutName)) return;
      this.currentLayout = layoutName;
      this.computeLayout(layoutName);
    }

    switchLayout(layoutName) {
      return this.setLayout(layoutName);
    }

    computeLayout(layoutName) {
      this.targetPositions.clear();
      this.startPositions.clear();

      for (const n of this.nodes) {
        this.startPositions.set(n.id, { x: n.x, y: n.y });
      }

      if (layoutName === 'islands') {
        for (const n of this.nodes) {
          this.targetPositions.set(n.id, {
            x: typeof n.x_island === 'number' ? n.x_island : 0,
            y: typeof n.y_island === 'number' ? n.y_island : 0,
          });
        }
      } else if (layoutName === 'pipeline') {
        for (const n of this.nodes) {
          this.targetPositions.set(n.id, {
            x: typeof n.x_pipeline === 'number' ? n.x_pipeline : 0,
            y: typeof n.y_pipeline === 'number' ? n.y_pipeline : 0,
          });
        }
      } else if (layoutName === 'radar') {
        for (const n of this.nodes) {
          this.targetPositions.set(n.id, {
            x: typeof n.x_radar === 'number' ? n.x_radar : 0,
            y: typeof n.y_radar === 'number' ? n.y_radar : 0,
          });
        }
      } else if (layoutName === 'tree') {
        this._computeTreeLayoutPositions();
      } else if (layoutName === 'force') {
        this._initForcePositions();
      }

      this.isTransitioning = true;
      this.transitionProgress = 0.0;
      this.transitionStartTime = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    }

    _computeTreeLayoutPositions() {
      // Root node: selected node or default god node
      const rootId = this.selectedNodeId || 'd09_mt5_trader';
      const rootNode = this.nodeMap.get(rootId) || this.nodes[0];
      if (!rootNode) return;

      const callers = Array.from(this.callersMap.get(rootNode.id) || []).filter(id => id !== rootNode.id);
      const dependencies = Array.from(this.dependenciesMap.get(rootNode.id) || []).filter(id => id !== rootNode.id && !callers.includes(id));

      const placedIds = new Set();
      placedIds.add(rootNode.id);
      this.targetPositions.set(rootNode.id, { x: 0, y: 0 });

      // Callers upstream above (y < 0)
      const cCount = callers.length;
      for (let i = 0; i < cCount; i++) {
        const cid = callers[i];
        const x = (i - (cCount - 1) / 2) * 220;
        this.targetPositions.set(cid, { x, y: -280 });
        placedIds.add(cid);
      }

      // Dependencies downstream below (y > 0)
      const dCount = dependencies.length;
      for (let i = 0; i < dCount; i++) {
        const did = dependencies[i];
        const x = (i - (dCount - 1) / 2) * 220;
        this.targetPositions.set(did, { x, y: 280 });
        placedIds.add(did);
      }

      // Remaining unplaced nodes arranged in outer circular wings
      const remaining = this.nodes.filter(n => !placedIds.has(n.id));
      const rCount = remaining.length;
      const wingRadius = 900;
      for (let i = 0; i < rCount; i++) {
        const angle = (i / Math.max(1, rCount)) * 2 * Math.PI;
        this.targetPositions.set(remaining[i].id, {
          x: Math.cos(angle) * wingRadius * 1.5,
          y: Math.sin(angle) * wingRadius,
        });
      }
    }

    _initForcePositions() {
      this.forceRunning = true;
      this.forceVelocities.clear();
      for (const n of this.nodes) {
        this.forceVelocities.set(n.id, { vx: 0, vy: 0 });
        // Start near island coordinates with small jitter
        const x = (n.x_island || 0) + (Math.random() - 0.5) * 40;
        const y = (n.y_island || 0) + (Math.random() - 0.5) * 40;
        this.targetPositions.set(n.id, { x, y });
      }
    }

    applyLayout(layoutName, animate = true) {
      this.currentLayout = layoutName;
      this.computeLayout(layoutName);
      if (!animate) {
        for (const n of this.nodes) {
          const tp = this.targetPositions.get(n.id);
          if (tp) {
            n.x = tp.x;
            n.y = tp.y;
          }
        }
        this.isTransitioning = false;
        this.transitionProgress = 1.0;
      }
    }

    // --- Physics & Force Simulation Step ---

    _stepForceSimulation() {
      if (this.currentLayout !== 'force') return;

      const kRepel = 28000;
      const kSpring = 0.04;
      const restLength = 160;
      const damping = 0.85;
      const centerGravity = 0.0008;

      const forces = new Map();
      for (const n of this.nodes) {
        forces.set(n.id, { fx: -n.x * centerGravity, fy: -n.y * centerGravity });
      }

      // Coulomb Repulsion between nodes
      const count = this.nodes.length;
      for (let i = 0; i < count; i++) {
        const a = this.nodes[i];
        for (let j = i + 1; j < count; j++) {
          const b = this.nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distSq = dx * dx + dy * dy || 1;
          const dist = Math.sqrt(distSq);
          if (dist < 800) {
            const f = kRepel / distSq;
            const fx = (dx / dist) * f;
            const fy = (dy / dist) * f;
            const fa = forces.get(a.id);
            const fb = forces.get(b.id);
            fa.fx += fx; fa.fy += fy;
            fb.fx -= fx; fb.fy -= fy;
          }
        }
      }

      // Spring Attraction along edges
      for (const e of this.edges) {
        const a = this.nodeMap.get(e.source);
        const b = this.nodeMap.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 1;
        const f = (dist - restLength) * kSpring;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        const fa = forces.get(a.id);
        const fb = forces.get(b.id);
        fa.fx += fx; fa.fy += fy;
        fb.fx -= fx; fb.fy -= fy;
      }

      // Integrate velocity and position
      for (const n of this.nodes) {
        const vel = this.forceVelocities.get(n.id) || { vx: 0, vy: 0 };
        const f = forces.get(n.id);
        vel.vx = (vel.vx + f.fx) * damping;
        vel.vy = (vel.vy + f.fy) * damping;
        n.x += Math.max(-25, Math.min(25, vel.vx));
        n.y += Math.max(-25, Math.min(25, vel.vy));
        // Clamp to world bounding arena
        n.x = Math.max(-4000, Math.min(4000, n.x));
        n.y = Math.max(-3000, Math.min(3000, n.y));
      }
    }

    _updateTransitions(now) {
      if (!this.isTransitioning) return;
      const elapsed = now - this.transitionStartTime;
      let t = elapsed / this.transitionDuration;
      if (t >= 1.0) {
        t = 1.0;
        this.isTransitioning = false;
      }

      // Smooth ease-in-out cubic
      const ease = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

      for (const n of this.nodes) {
        const s = this.startPositions.get(n.id);
        const target = this.targetPositions.get(n.id);
        if (s && target) {
          n.x = s.x + (target.x - s.x) * ease;
          n.y = s.y + (target.y - s.y) * ease;
        }
      }
    }

    _updateCameraPhysics() {
      this.cameraX += (this.targetCameraX - this.cameraX) * this.lerpFactor;
      this.cameraY += (this.targetCameraY - this.cameraY) * this.lerpFactor;
      this.zoom += (this.targetZoom - this.zoom) * this.lerpFactor;
    }

    // --- Ambient Cosmic Particles ---

    _initParticles(count) {
      this.particles = [];
      for (let i = 0; i < count; i++) {
        this.particles.push({
          x: (Math.random() - 0.5) * 6000,
          y: (Math.random() - 0.5) * 4000,
          vx: (Math.random() - 0.5) * 0.4,
          vy: (Math.random() - 0.5) * 0.4,
          radius: 0.8 + Math.random() * 1.6,
          alpha: 0.15 + Math.random() * 0.4,
          twinkleSpeed: 0.02 + Math.random() * 0.03,
          twinklePhase: Math.random() * Math.PI * 2,
        });
      }
    }

    _updateParticles() {
      for (const p of this.particles) {
        p.x += p.vx;
        p.y += p.vy;
        p.twinklePhase += p.twinkleSpeed;
        if (p.x < -3000) p.x = 3000;
        if (p.x > 3000) p.x = -3000;
        if (p.y < -2000) p.y = 2000;
        if (p.y > 2000) p.y = -2000;
      }
    }

    // --- Trade Lifecycle Simulation Integration ---

    highlightLifecycleStage(stageIndex) {
      this.activeStageIndex = stageIndex;
      this.activeStageNodeIds.clear();
      this.activeStageEdges = [];

      const stage = this.lifecycleStages.find(s => s.stage_index === stageIndex);
      if (!stage) return;

      for (const nid of stage.active_nodes || []) {
        this.activeStageNodeIds.add(nid);
      }
      this.activeStageEdges = stage.active_edges || [];

      // Auto-center camera on active nodes cluster
      if (stage.active_nodes && stage.active_nodes.length > 0) {
        let avgX = 0, avgY = 0, valid = 0;
        for (const nid of stage.active_nodes) {
          const n = this.nodeMap.get(nid);
          if (n) {
            avgX += n.x;
            avgY += n.y;
            valid++;
          }
        }
        if (valid > 0) {
          this.glideTo(avgX / valid, avgY / valid, 1.1);
        }
      }
    }

    clearHighlights() {
      this.activeStageIndex = null;
      this.activeStageNodeIds.clear();
      this.activeStageEdges = [];
      this.selectedNodeId = null;
      this.hoveredNodeId = null;
    }

    // --- Pill Dimensions & Hit Testing ---

    getNodePillDimensions(node) {
      const label = node.name || node.id;
      // Estimate width based on character count (avoid measuring in loop if possible)
      const approxCharWidth = 7.5;
      const textWidth = Math.max(60, label.length * approxCharWidth);
      const width = textWidth + 46; // dot + padding
      const height = 32;
      return { width, height };
    }

    getNodeAtWorld(wx, wy) {
      // Iterate backwards so top-drawn nodes are hit first
      for (let i = this.nodes.length - 1; i >= 0; i--) {
        const n = this.nodes[i];
        if (this.hiddenSubsystems && this.hiddenSubsystems.has(n.subsystem_id)) {
          continue;
        }
        const { width, height } = this.getNodePillDimensions(n);
        const left = n.x - width / 2;
        const right = n.x + width / 2;
        const top = n.y - height / 2;
        const bottom = n.y + height / 2;
        if (wx >= left && wx <= right && wy >= top && wy <= bottom) {
          return n;
        }
      }
      return null;
    }

    // --- Main Render Loop (60 FPS) ---

    render(timestamp = 0) {
      const now = timestamp || (typeof performance !== 'undefined' ? performance.now() : Date.now());
      this._updateTransitions(now);
      this._updateCameraPhysics();
      if (this.currentLayout === 'force') {
        this._stepForceSimulation();
      }
      this._updateParticles();

      if (!this.ctx) return;

      const ctx = this.ctx;
      ctx.save();

      // Clear main canvas with cyber background
      ctx.fillStyle = '#080c14';
      ctx.fillRect(0, 0, this.width, this.height);

      // 1. Draw Ambient Coordinate Grid & Cosmic Particles
      this._drawCoordinateGrid(ctx);
      this._drawParticles(ctx);

      // 2. Draw Edges with Bezier curves & animated pulses
      this._drawEdges(ctx);

      // 3. Draw Nodes as rounded pills with glow and indicators
      this._drawNodes(ctx);

      ctx.restore();

      // 4. Render Minimap Radar HUD
      this._renderMinimap();

      if (this.isRunning) {
        this.animFrameId = requestAnimationFrame(this.render.bind(this));
      }
    }

    _drawCoordinateGrid(ctx) {
      const gridSize = 120;
      const screenTL = this.screenToWorld(0, 0);
      const screenBR = this.screenToWorld(this.width, this.height);

      const startX = Math.floor(screenTL.x / gridSize) * gridSize;
      const endX = Math.ceil(screenBR.x / gridSize) * gridSize;
      const startY = Math.floor(screenTL.y / gridSize) * gridSize;
      const endY = Math.ceil(screenBR.y / gridSize) * gridSize;

      ctx.lineWidth = 1;
      ctx.strokeStyle = 'rgba(30, 41, 59, 0.35)';
      ctx.beginPath();

      for (let x = startX; x <= endX; x += gridSize) {
        const { x: sx } = this.worldToScreen(x, 0);
        ctx.moveTo(sx, 0);
        ctx.lineTo(sx, this.height);
      }
      for (let y = startY; y <= endY; y += gridSize) {
        const { y: sy } = this.worldToScreen(0, y);
        ctx.moveTo(0, sy);
        ctx.lineTo(this.width, sy);
      }
      ctx.stroke();

      // Major world origin axis
      const origin = this.worldToScreen(0, 0);
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.2)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(origin.x, 0); ctx.lineTo(origin.x, this.height);
      ctx.moveTo(0, origin.y); ctx.lineTo(this.width, origin.y);
      ctx.stroke();
    }

    _drawParticles(ctx) {
      for (const p of this.particles) {
        const { x: sx, y: sy } = this.worldToScreen(p.x, p.y);
        if (sx < -10 || sx > this.width + 10 || sy < -10 || sy > this.height + 10) continue;
        const alpha = Math.max(0.05, p.alpha * (0.7 + 0.3 * Math.sin(p.twinklePhase)));
        ctx.fillStyle = `rgba(56, 189, 248, ${alpha})`;
        ctx.beginPath();
        ctx.arc(sx, sy, p.radius * Math.max(0.5, this.zoom), 0, Math.PI * 2);
        ctx.fill();
      }
    }

    _drawEdges(ctx) {
      const focusId = this.hoveredNodeId || this.selectedNodeId;
      const callers = focusId ? (this.callersMap.get(focusId) || new Set()) : null;
      const deps = focusId ? (this.dependenciesMap.get(focusId) || new Set()) : null;

      for (const edge of this.edges) {
        const src = this.nodeMap.get(edge.source);
        const dst = this.nodeMap.get(edge.target);
        if (!src || !dst) continue;
        if (this.hiddenSubsystems && (this.hiddenSubsystems.has(src.subsystem_id) || this.hiddenSubsystems.has(dst.subsystem_id))) {
          continue;
        }

        // Viewport culling check
        const s1 = this.worldToScreen(src.x, src.y);
        const s2 = this.worldToScreen(dst.x, dst.y);
        const minX = Math.min(s1.x, s2.x), maxX = Math.max(s1.x, s2.x);
        const minY = Math.min(s1.y, s2.y), maxY = Math.max(s1.y, s2.y);
        if (maxX < -50 || minX > this.width + 50 || maxY < -50 || minY > this.height + 50) {
          continue;
        }

        // Highlight status
        const isConnectedToFocus = focusId && (edge.source === focusId || edge.target === focusId);
        const isLifecycleActive = this.activeStageEdges.some(
          e => e.source === edge.source && e.target === edge.target
        );

        let alpha = 0.22;
        let strokeColor = 'rgba(71, 85, 105, 0.4)';
        let lineWidth = 1.2 * this.zoom;

        if (isLifecycleActive) {
          alpha = 0.95;
          strokeColor = '#38bdf8';
          lineWidth = 2.4 * this.zoom;
        } else if (isConnectedToFocus) {
          alpha = 0.9;
          strokeColor = edge.target === focusId ? '#10b981' : '#f43f5e'; // Green if caller->focus, Rose if focus->dep
          lineWidth = 2.2 * this.zoom;
        } else if (focusId) {
          alpha = 0.08;
          strokeColor = 'rgba(51, 65, 85, 0.2)';
        }

        // Calculate smooth Bezier curve control points
        const { cp1, cp2 } = this._getBezierControlPoints(s1.x, s1.y, s2.x, s2.y);

        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = Math.max(0.8, lineWidth);
        ctx.beginPath();
        ctx.moveTo(s1.x, s1.y);
        ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, s2.x, s2.y);
        ctx.stroke();

        // Animated Signal Pulses along active or connected edges
        const shouldPulse = isLifecycleActive || isConnectedToFocus || (!focusId && edge.weight && edge.weight > 1);
        if (shouldPulse) {
          const speedMultiplier = isLifecycleActive ? 2.5 : 1.0;
          edge.pulseProgress = (edge.pulseProgress + edge.pulseSpeed * speedMultiplier) % 1.0;
          this._drawBezierPulse(ctx, s1, cp1, cp2, s2, edge.pulseProgress, strokeColor);
        }
      }
    }

    _getBezierControlPoints(x1, y1, x2, y2) {
      if (this.currentLayout === 'pipeline') {
        // Horizontal S-curve
        const dx = Math.abs(x2 - x1) * 0.5;
        return {
          cp1: { x: x1 + dx, y: y1 },
          cp2: { x: x2 - dx, y: y2 },
        };
      } else {
        // Subtle perpendicular arc
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2;
        const dx = x2 - x1;
        const dy = y2 - y1;
        const dist = Math.hypot(dx, dy) || 1;
        const curv = Math.min(45, dist * 0.12);
        const nx = -dy / dist;
        const ny = dx / dist;
        return {
          cp1: { x: mx + nx * curv, y: my + ny * curv },
          cp2: { x: mx + nx * curv, y: my + ny * curv },
        };
      }
    }

    _drawBezierPulse(ctx, p0, cp1, cp2, p3, t, color) {
      const u = 1 - t;
      const px = u * u * u * p0.x + 3 * u * u * t * cp1.x + 3 * u * t * t * cp2.x + t * t * t * p3.x;
      const py = u * u * u * p0.y + 3 * u * u * t * cp1.y + 3 * u * t * t * cp2.y + t * t * t * p3.y;

      const r = Math.max(2, 3.5 * Math.min(1.5, this.zoom));
      ctx.save();
      ctx.shadowColor = color;
      ctx.shadowBlur = 8;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    _drawNodes(ctx) {
      const focusId = this.hoveredNodeId || this.selectedNodeId;
      const callers = focusId ? (this.callersMap.get(focusId) || new Set()) : null;
      const deps = focusId ? (this.dependenciesMap.get(focusId) || new Set()) : null;

      for (const node of this.nodes) {
        if (this.hiddenSubsystems && this.hiddenSubsystems.has(node.subsystem_id)) {
          continue;
        }
        const { x: sx, y: sy } = this.worldToScreen(node.x, node.y);
        const { width: rawW, height: rawH } = this.getNodePillDimensions(node);
        const w = rawW * this.zoom;
        const h = rawH * this.zoom;

        // Viewport culling
        if (sx + w / 2 < -50 || sx - w / 2 > this.width + 50 || sy + h / 2 < -50 || sy - h / 2 > this.height + 50) {
          continue;
        }

        const isFocus = node.id === focusId;
        const isCaller = callers && callers.has(node.id);
        const isDep = deps && deps.has(node.id);
        const isLifecycleActive = this.activeStageNodeIds.has(node.id);

        let alpha = 1.0;
        let borderColor = '#1e293b';
        let glowColor = null;

        if (isFocus) {
          borderColor = '#38bdf8';
          glowColor = '#38bdf8';
        } else if (isCaller) {
          borderColor = '#10b981'; // Emerald Green
          glowColor = '#10b981';
        } else if (isDep) {
          borderColor = '#f43f5e'; // Rose Red
          glowColor = '#f43f5e';
        } else if (isLifecycleActive) {
          borderColor = '#38bdf8'; // Radiant sky cyan
          glowColor = '#38bdf8';
        } else if (focusId || (this.activeStageNodeIds && this.activeStageNodeIds.size > 0)) {
          alpha = 0.22; // Soft dimming for unrelated nodes during inspection or tour
        }

        const subColor = SUBSYSTEM_COLORS[node.subsystem_id] || DEFAULT_COLOR;

        ctx.save();
        ctx.globalAlpha = alpha;

        // Draw pill background
        const rx = sx - w / 2;
        const ry = sy - h / 2;
        const radius = Math.min(16 * this.zoom, h / 2);

        if (glowColor) {
          ctx.shadowColor = glowColor;
          ctx.shadowBlur = 12 * Math.min(2, this.zoom);
        }

        ctx.fillStyle = '#0f172a';
        ctx.strokeStyle = borderColor;
        ctx.lineWidth = Math.max(1, (isFocus ? 2.5 : 1.2) * this.zoom);

        this._drawRoundedRect(ctx, rx, ry, w, h, radius);
        ctx.fill();
        ctx.stroke();

        // Radiant neon pulse ring for active lifecycle stage nodes
        if (isLifecycleActive) {
          ctx.save();
          const ringPad = 8 * this.zoom + Math.sin(Date.now() / 180) * 3 * this.zoom;
          const ringRadius = radius + ringPad;
          ctx.strokeStyle = 'rgba(56, 189, 248, 0.45)';
          ctx.lineWidth = Math.max(1, 1.6 * this.zoom);
          this._drawRoundedRect(ctx, rx - ringPad, ry - ringPad, w + ringPad * 2, h + ringPad * 2, ringRadius);
          ctx.stroke();
          ctx.restore();
        }

        ctx.shadowBlur = 0; // reset shadow

        // Subsystem indicator dot
        const dotRadius = Math.max(2, 4.5 * this.zoom);
        const dotX = rx + 14 * this.zoom;
        const dotY = sy;
        ctx.fillStyle = subColor;
        ctx.beginPath();
        ctx.arc(dotX, dotY, dotRadius, 0, Math.PI * 2);
        ctx.fill();

        // Node Label Typography (clean, crisp monospace)
        if (this.zoom > 0.35) {
          const fontSize = Math.max(8, Math.round(12 * this.zoom));
          ctx.font = `600 ${fontSize}px "JetBrains Mono", "Fira Code", monospace`;
          ctx.fillStyle = isFocus ? '#f8fafc' : '#cbd5e1';
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';

          const textX = dotX + 8 * this.zoom;
          const maxTextW = w - (textX - rx) - 8 * this.zoom;

          let label = node.name || node.id;
          ctx.fillText(label, textX, dotY, Math.max(10, maxTextW));
        }

        ctx.restore();
      }
    }

    _drawRoundedRect(ctx, x, y, width, height, radius) {
      ctx.beginPath();
      ctx.moveTo(x + radius, y);
      ctx.lineTo(x + width - radius, y);
      ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
      ctx.lineTo(x + width, y + height - radius);
      ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
      ctx.lineTo(x + radius, y + height);
      ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
      ctx.lineTo(x, y + radius);
      ctx.quadraticCurveTo(x, y, x + radius, y);
      ctx.closePath();
    }

    // --- Minimap Radar HUD Rendering ---

    _renderMinimap() {
      if (!this.radarCtx) return;
      const rctx = this.radarCtx;
      const rw = this.radarWidth;
      const rh = this.radarHeight;

      rctx.clearRect(0, 0, rw, rh);

      // Radar background glass styling
      rctx.fillStyle = '#080c14';
      rctx.fillRect(0, 0, rw, rh);
      rctx.strokeStyle = '#1e293b';
      rctx.lineWidth = 1;
      rctx.strokeRect(0, 0, rw, rh);

      // Radar sweep reticle lines
      rctx.strokeStyle = 'rgba(56, 189, 248, 0.15)';
      rctx.beginPath();
      rctx.moveTo(rw / 2, 0); rctx.lineTo(rw / 2, rh);
      rctx.moveTo(0, rh / 2); rctx.lineTo(rw, rh / 2);
      rctx.stroke();

      // Draw node dots on minimap
      for (const node of this.nodes) {
        if (this.hiddenSubsystems && this.hiddenSubsystems.has(node.subsystem_id)) {
          continue;
        }
        const { x: mx, y: my } = this.worldToMinimap(node.x, node.y);
        if (mx < 0 || mx > rw || my < 0 || my > rh) continue;

        const subColor = SUBSYSTEM_COLORS[node.subsystem_id] || DEFAULT_COLOR;
        rctx.fillStyle = subColor;
        rctx.beginPath();
        rctx.arc(mx, my, 1.8, 0, Math.PI * 2);
        rctx.fill();
      }

      // Draw Camera Frustum Viewport Rectangle
      const tl = this.screenToWorld(0, 0);
      const br = this.screenToWorld(this.width, this.height);
      const mtl = this.worldToMinimap(tl.x, tl.y);
      const mbr = this.worldToMinimap(br.x, br.y);

      const fw = Math.abs(mbr.x - mtl.x);
      const fh = Math.abs(mbr.y - mtl.y);
      const fx = Math.min(mtl.x, mbr.x);
      const fy = Math.min(mtl.y, mbr.y);

      rctx.fillStyle = 'rgba(56, 189, 248, 0.12)';
      rctx.strokeStyle = 'rgba(56, 189, 248, 0.8)';
      rctx.lineWidth = 1.2;
      rctx.fillRect(fx, fy, fw, fh);
      rctx.strokeRect(fx, fy, fw, fh);
    }

    // --- Lifecycle Management ---

    start() {
      if (this.isRunning) return;
      this.isRunning = true;
      this.lastTimestamp = (typeof performance !== 'undefined' ? performance.now() : Date.now());
      this.render();
    }

    stop() {
      this.isRunning = false;
      if (this.animFrameId) {
        cancelAnimationFrame(this.animFrameId);
        this.animFrameId = null;
      }
    }

    destroy() {
      this.stop();
      this._unbindEvents();
    }
  }

  // Universal Module Export (Browser & CommonJS / Node)
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = CanvasEngine;
  }
  if (typeof global !== 'undefined') {
    global.CanvasEngine = CanvasEngine;
  }
})(typeof window !== 'undefined' ? window : globalThis);
