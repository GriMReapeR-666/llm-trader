# Alpha Factor Mining Integration Plan

**Paper:** "Alpha Factor Mining Using Hierarchical Genetic Algorithms with Quality-Diversity Selection"
**Author:** Junyoung Jung
**Target:** LLM Trader System

---

## Executive Summary

Integrate hierarchical genetic algorithm (GA) with PCA-grid quality-diversity selection for autonomous alpha factor discovery. The system will generate 3,000+ alpha expressions, evolve them through crossover/mutation, enforce diversity via PCA behavior space, and aggregate top candidates into a "MegaAlpha" portfolio using deep neural networks.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    ALPHA MINING PIPELINE                        │
├─────────────────────────────────────────────────────────────────┤
│ 1. Expression Generator ──→ 2. GA Evolution Engine             │
│    (3,000 depth-1)           (crossover/mutation)              │
│         │                           │                          │
│         ▼                           ▼                          │
│ 3. IC Evaluator ←────────── 4. PCA Behavior Projector         │
│    (Spearman rank)            (2D behavior space)              │
│         │                           │                          │
│         ▼                           ▼                          │
│ 5. Grid Selection ←──────── 6. Diversity Enforcer              │
│    (top-|IC| per cell)        (correlation guard)              │
│         │                           │                          │
│         ▼                           ▼                          │
│ 7. DNN Aggregator ←──────── 8. SHAP Explainer                 │
│    (MegaAlpha portfolio)      (factor attribution)             │
│         │                           │                          │
│         ▼                           ▼                          │
│ 9. Strategy Factory ←────── 10. Risk Management                │
│    (lifecycle gates)           (position sizing)               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Core Alpha Mining Engine

### 1.1 Create Alpha Expression Tree Data Structure

**File:** `research/alpha_miner/expression_tree.py`

```python
@dataclass
class AlphaExpression:
    """Node in an alpha expression tree."""
    operator: str                    # e.g., "ts_mean", "cs_rank", "add"
    children: List['AlphaExpression']
    field: Optional[str] = None      # e.g., "close", "volume"
    window: Optional[int] = None     # For time-series operators
    depth: int = 0
    
    def evaluate(self, ohlcv: pd.DataFrame) -> pd.Series:
        """Recursively evaluate the expression tree."""
        pass
    
    def to_string(self) -> str:
        """Human-readable expression string."""
        pass
    
    def copy(self) -> 'AlphaExpression':
        """Deep copy for genetic operations."""
        pass
```

**Operators to implement:**

| Category | Operators | Window Range |
|----------|-----------|--------------|
| **Arithmetic** | `add`, `subtract`, `multiply`, `divide` | N/A |
| **Transformation** | `normalize`, `scale`, `abs`, `sign`, `log` | N/A |
| **Time-Series** | `ts_delay`, `ts_delta`, `ts_returns`, `ts_mean`, `ts_std`, `ts_rank`, `ts_min`, `ts_max`, `ts_argmin`, `ts_argmax` | 5-100 |
| **Cross-Sectional** | `cs_rank`, `cs_scale`, `cs_normalize` | N/A |

### 1.2 Expression Generator

**File:** `research/alpha_miner/expression_generator.py`

```python
class ExpressionGenerator:
    """Generate random alpha expressions of specified depth."""
    
    FIELDS = ["open", "high", "low", "close", "volume"]
    OPERATORS = {
        "unary": ["abs", "sign", "log", "negate"],
        "binary": ["add", "subtract", "multiply", "divide"],
        "ts_unary": ["ts_mean", "ts_std", "ts_rank", "ts_min", "ts_max"],
        "cs_unary": ["cs_rank", "cs_scale", "cs_normalize"],
    }
    
    def generate(self, depth: int = 1, max_depth: int = 5) -> AlphaExpression:
        """Generate a random expression tree up to max_depth."""
        pass
    
    def generate_population(self, size: int = 3000, depth: int = 1) -> List[AlphaExpression]:
        """Generate initial population of depth-1 expressions."""
        pass
```

### 1.3 IC Evaluator

**File:** `research/alpha_miner/ic_evaluator.py`

```python
class ICEvaluator:
    """Calculate Information Coefficient for alpha expressions."""
    
    def calculate_ic(self, alpha_values: pd.Series, 
                     forward_returns: pd.Series) -> float:
        """Spearman rank correlation between alpha and forward returns."""
        pass
    
    def calculate_rolling_ic(self, alpha_values: pd.Series,
                            forward_returns: pd.Series,
                            window: int = 20) -> pd.Series:
        """Rolling IC over specified window."""
        pass
    
    def calculate_ic_ir(self, ic_series: pd.Series) -> float:
        """IC Information Ratio = mean(IC) / std(IC)."""
        pass
```

---

## Phase 2: GA Evolution Engine

### 2.1 Genetic Operators

**File:** `research/alpha_miner/genetic_operators.py`

```python
class GeneticOperators:
    """Crossover and mutation for alpha expression trees."""
    
    def crossover(self, parent1: AlphaExpression, 
                  parent2: AlphaExpression) -> Tuple[AlphaExpression, AlphaExpression]:
        """Subtree crossover: swap random subtrees between parents."""
        pass
    
    def mutate(self, expression: AlphaExpression,
               mutation_rate: float = 0.1) -> AlphaExpression:
        """Point mutation: randomly change operator, field, or window."""
        pass
    
    def subtree_mutation(self, expression: AlphaExpression,
                         max_depth: int = 5) -> AlphaExpression:
        """Replace random subtree with new random expression."""
        pass
```

### 2.2 GA Evolution Loop

**File:** `research/alpha_miner/ga_engine.py`

```python
class GAEngine:
    """Hierarchical GA with quality-diversity selection."""
    
    def __init__(self, 
                 population_size: int = 3000,
                 generations: int = 5,
                 depth_increment: int = 1,
                 max_depth: int = 5):
        self.population_size = population_size
        self.generations = generations
        self.depth_increment = depth_increment
        self.max_depth = max_depth
    
    def evolve(self, ohlcv: pd.DataFrame, 
               forward_returns: pd.Series) -> List[AlphaExpression]:
        """Run GA evolution and return best alpha expressions."""
        pass
    
    def _evaluate_population(self, population: List[AlphaExpression],
                            ohlcv: pd.DataFrame,
                            forward_returns: pd.Series) -> Dict[str, float]:
        """Evaluate IC for all expressions in population."""
        pass
```

---

## Phase 3: PCA Behavior Space & Grid Selection

### 3.1 PCA Behavior Projector

**File:** `research/alpha_miner/pca_projector.py`

```python
class PCAProjector:
    """Project alpha behaviors into 2D PCA space."""
    
    def __init__(self, n_components: int = 2):
        self.n_components = n_components
    
    def fit_transform(self, alpha_panel: pd.DataFrame) -> np.ndarray:
        """
        Input: alpha_panel shape (n_timepoints, n_alphas)
        Output: B shape (n_alphas, 2) - 2D behavior coordinates
        """
        pass
    
    def get_behavior_grid(self, behavior_coords: np.ndarray,
                         grid_size: int = 10) -> Dict[Tuple[int, int], List[int]]:
        """Map alphas to grid cells in 2D behavior space."""
        pass
```

### 3.2 Grid Selection

**File:** `research/alpha_miner/grid_selector.py`

```python
class GridSelector:
    """Quality-diversity selection via PCA grid."""
    
    def __init__(self, grid_size: int = 10, parent_pool_size: int = 150):
        self.grid_size = grid_size
        self.parent_pool_size = parent_pool_size
    
    def select(self, alpha_ics: Dict[str, float],
               behavior_coords: np.ndarray) -> List[str]:
        """
        Select top-|IC| candidates per grid cell.
        Maintains diversity by spreading selections across behavior space.
        """
        pass
    
    def _select_per_cell(self, cell_alphas: List[str],
                        alpha_ics: Dict[str, float]) -> List[str]:
        """Select best alphas within a single grid cell."""
        pass
```

---

## Phase 4: DNN Aggregation & MegaAlpha

### 4.1 DNN Aggregator

**File:** `research/alpha_miner/dnn_aggregator.py`

```python
class DNNAggregator:
    """Deep Neural Network for alpha factor aggregation."""
    
    def __init__(self, input_dim: int, hidden_dims: List[int] = [128, 64, 32]):
        self.model = self._build_model(input_dim, hidden_dims)
    
    def _build_model(self, input_dim: int, 
                     hidden_dims: List[int]) -> nn.Module:
        """Build PyTorch DNN model."""
        pass
    
    def train(self, alpha_panel: pd.DataFrame,
              labels: pd.Series,
              epochs: int = 100,
              batch_size: int = 32) -> Dict[str, float]:
        """Train DNN on alpha factors to predict 5-day returns."""
        pass
    
    def predict(self, alpha_panel: pd.DataFrame) -> np.ndarray:
        """Predict probability of positive 5-day return."""
        pass
    
    def get_portfolio_weights(self, predictions: np.ndarray) -> np.ndarray:
        """Map predictions to long-only portfolio weights."""
        pass
```

### 4.2 SHAP Explainer

**File:** `research/alpha_miner/shap_explainer.py`

```python
class SHAPExplainer:
    """Explain DNN predictions using SHAP values."""
    
    def __init__(self, model: nn.Module, background_data: pd.DataFrame):
        self.explainer = shap.DeepExplainer(model, background_data)
    
    def explain(self, alpha_panel: pd.DataFrame) -> Dict[str, float]:
        """Get SHAP values for each alpha factor."""
        pass
    
    def get_dominant_signals(self, shap_values: Dict[str, float],
                            top_k: int = 10) -> List[Tuple[str, float]]:
        """Return top-K alpha factors by absolute SHAP value."""
        pass
```

---

## Phase 5: Integration with Strategy Factory

### 5.1 Alpha Strategy Bridge

**File:** `research/alpha_miner/strategy_bridge.py`

```python
class AlphaStrategyBridge:
    """Bridge alpha mining results to strategy factory."""
    
    def __init__(self, factory: StrategyFactory):
        self.factory = factory
    
    def register_alpha_strategy(self, alpha: AlphaExpression,
                               ic_metrics: Dict[str, float],
                               shap_values: Dict[str, float]) -> str:
        """Register mined alpha as a new strategy candidate."""
        pass
    
    def update_strategy_metrics(self, strategy_id: str,
                               backtest_results: Dict[str, float]) -> None:
        """Update strategy metrics for factory gate evaluation."""
        pass
    
    def promote_strategy(self, strategy_id: str) -> bool:
        """Promote strategy through factory gates."""
        pass
```

### 5.2 Integration with Agent Scheduler

**File:** Add to `operations/agent_scheduler.py`

```python
# New schedule for alpha mining
SCHEDULES.append({
    "name": "alpha_mining_sprint",
    "agent": "ResearchAgent",
    "interval": 604800,  # Weekly
    "payload": {"kind": "alpha_mining_sprint"}
})
```

**File:** Add to `operations/agents/research_agent.py`

```python
def handle_alpha_mining_sprint(payload: Dict[str, Any], 
                               context: Any = None) -> Dict[str, Any]:
    """Executor for alpha mining schedule."""
    from research.alpha_miner.ga_engine import GAEngine
    from research.alpha_miner.pca_projector import PCAProjector
    from research.alpha_miner.dnn_aggregator import DNNAggregator
    from research.alpha_miner.strategy_bridge import AlphaStrategyBridge
    
    # 1. Load OHLCV data
    ohlcv = load_ohlcv_data(context)
    
    # 2. Run GA evolution
    ga = GAEngine(population_size=3000, generations=5)
    alpha_expressions = ga.evolve(ohlcv, forward_returns)
    
    # 3. PCA projection & grid selection
    projector = PCAProjector(n_components=2)
    selected_alphas = projector.select(alpha_expressions)
    
    # 4. DNN aggregation
    aggregator = DNNAggregator(input_dim=len(selected_alphas))
    mega_alpha = aggregator.train(selected_alphas, labels)
    
    # 5. Register in strategy factory
    bridge = AlphaStrategyBridge(get_factory())
    strategy_id = bridge.register_alpha_strategy(mega_alpha, metrics)
    
    return {"strategy_id": strategy_id, "alphas_mined": len(alpha_expressions)}
```

---

## Phase 6: Integration with Risk Management

### 6.1 Alpha Risk Overlay

**File:** `research/alpha_miner/alpha_risk.py`

```python
class AlphaRiskManager:
    """Risk management for alpha-mined strategies."""
    
    def __init__(self, risk_manager: RiskManager):
        self.risk_manager = risk_manager
    
    def assess_alpha_risk(self, alpha: AlphaExpression,
                         ic_metrics: Dict[str, float],
                         backtest_results: Dict[str, float]) -> Dict[str, Any]:
        """Assess risk characteristics of mined alpha."""
        pass
    
    def get_sizing_adjustment(self, alpha_quality: Dict[str, float]) -> float:
        """Return position sizing multiplier based on alpha quality."""
        pass
    
    def check_alpha_decay(self, alpha_id: str,
                         ic_history: List[float]) -> bool:
        """Check if alpha IC is decaying."""
        pass
```

### 6.2 Integration with RiskManager

**File:** Modify `core/risk_manager.py`

```python
def calc_position_size(self, symbol: str, entry: float, sl: float,
                       confidence: float = 1.0,
                       alpha_quality: Optional[Dict[str, float]] = None,
                       ...) -> float:
    # ... existing code ...
    
    # Alpha quality overlay
    if alpha_quality is not None:
        from research.alpha_miner.alpha_risk import AlphaRiskManager
        alpha_risk = AlphaRiskManager(self)
        alpha_mult = alpha_risk.get_sizing_adjustment(alpha_quality)
        size_multiplier *= alpha_mult
    
    # ... rest of existing code ...
```

---

## Phase 7: Integration with LLM Orchestrator

### 7.1 Alpha Discovery Enrichment

**File:** Add to `intelligence/llm_orchestrator.py`

```python
def _apply_alpha_mining_enrichment(self, toon: Dict[str, Any]) -> Dict[str, Any]:
    """Enrich decision context with alpha mining results."""
    from research.alpha_miner.strategy_bridge import AlphaStrategyBridge
    
    bridge = AlphaStrategyBridge(get_factory())
    active_alphas = bridge.get_active_alpha_strategies()
    
    toon["alpha_mining_results"] = {
        "active_alpha_strategies": active_alphas,
        "mega_alpha_composite": self._compute_mega_alpha_composite(active_alphas),
        "alpha_diversity_score": self._compute_alpha_diversity(active_alphas),
        "alpha_decay_alerts": self._check_alpha_decay(active_alphas),
    }
    
    return toon
```

### 7.2 Prompt Enhancement

**File:** Modify `intelligence/llm_orchestrator.py` prompt template

```python
SIGNAL_SYSTEM_PROMPT_TEMPLATE = """
... existing prompt ...

## Alpha Mining Intelligence
The system continuously mines new alpha factors using hierarchical genetic algorithms.
Current active alpha strategies: {alpha_mining_results.active_alpha_strategies}
MegaAlpha composite signal: {alpha_mining_results.mega_alpha_composite}
Alpha diversity score: {alpha_mining_results.alpha_diversity_score}

Use these alpha signals to enhance your decision-making.
"""
```

---

## Phase 8: Testing & Validation

### 8.1 Unit Tests

**File:** `tests/test_alpha_miner.py`

```python
def test_expression_tree_generation():
    """Test random expression tree generation."""
    pass

def test_ic_calculation():
    """Test Information Coefficient calculation."""
    pass

def test_ga_evolution():
    """Test GA evolution produces better alphas."""
    pass

def test_pca_projection():
    """Test PCA behavior space projection."""
    pass

def test_grid_selection():
    """Test diversity-enforced grid selection."""
    pass

def test_dnn_aggregation():
    """Test DNN alpha aggregation."""
    pass
```

### 8.2 Integration Tests

**File:** `tests/test_alpha_integration.py`

```python
def test_alpha_mining_pipeline():
    """Test end-to-end alpha mining pipeline."""
    pass

def test_strategy_factory_integration():
    """Test alpha strategies register in factory."""
    pass

def test_risk_management_integration():
    """Test alpha risk overlays apply correctly."""
    pass
```

### 8.3 Acceptance Criteria

| Metric | Target | Current |
|--------|--------|---------|
| Average IC | ≥0.004 | 0.001 |
| Significant Alphas (|IC|>0.02) | ≥10% | 0% |
| Diversity Score | ≥0.7 | N/A |
| Portfolio Sharpe | ≥1.5 | N/A |
| Max Drawdown | ≤15% | 40% |

---

## Implementation Timeline

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 1: Expression Tree & Generator | 3 days | None |
| Phase 2: GA Evolution Engine | 4 days | Phase 1 |
| Phase 3: PCA & Grid Selection | 3 days | Phase 2 |
| Phase 4: DNN Aggregation | 4 days | Phase 3 |
| Phase 5: Strategy Factory Integration | 2 days | Phase 4 |
| Phase 6: Risk Management Integration | 2 days | Phase 5 |
| Phase 7: LLM Orchestrator Integration | 2 days | Phase 6 |
| Phase 8: Testing & Validation | 3 days | Phase 7 |

**Total Estimated Duration:** 23 days

---

## File Structure

```
research/alpha_miner/
├── __init__.py
├── expression_tree.py          # Alpha expression tree data structure
├── expression_generator.py     # Random expression generation
├── ic_evaluator.py             # Information Coefficient calculation
├── genetic_operators.py        # Crossover and mutation operators
├── ga_engine.py                # Main GA evolution loop
├── pca_projector.py            # PCA behavior space projection
├── grid_selector.py            # Quality-diversity grid selection
├── dnn_aggregator.py           # Deep neural network aggregation
├── shap_explainer.py           # SHAP-based explainability
├── strategy_bridge.py          # Bridge to strategy factory
├── alpha_risk.py               # Risk management for alphas
└── config.py                   # Configuration constants
```

---

## Configuration

**File:** `research/alpha_miner/config.py`

```python
ALPHA_MINING_CONFIG = {
    # GA Parameters
    "population_size": 3000,
    "generations": 5,
    "depth_increment": 1,
    "max_depth": 5,
    "mutation_rate": 0.1,
    "crossover_rate": 0.7,
    
    # PCA Grid Parameters
    "grid_size": 10,
    "parent_pool_size": 150,
    "n_pca_components": 2,
    
    # DNN Parameters
    "hidden_dims": [128, 64, 32],
    "epochs": 100,
    "batch_size": 32,
    "learning_rate": 0.001,
    
    # IC Thresholds
    "min_ic_threshold": 0.02,
    "good_ic_threshold": 0.05,
    "strong_ic_threshold": 0.10,
    
    # Risk Parameters
    "max_alpha_concentration": 0.25,
    "min_alpha_diversity": 0.7,
    "alpha_decay_threshold": 0.5,
}
```

---

## Success Metrics

1. **Alpha Quality:** Average IC ≥0.004 (4x improvement)
2. **Diversity:** Grid coverage ≥70% of behavior space
3. **Robustness:** Walk-forward IC stability ≥0.6
4. **Performance:** MegaAlpha portfolio Sharpe ≥1.5
5. **Risk:** Max drawdown ≤15% with position sizing
6. **Integration:** Seamless integration with existing strategy factory

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Overfitting | Walk-forward validation, out-of-sample testing |
| Computational cost | Parallel processing, batch evaluation |
| Expression bloat | Depth limits, parsimony pressure |
| Low diversity | PCA grid enforcement, correlation guards |
| Alpha decay | Continuous monitoring, automatic quarantine |

---

## Next Steps

1. **Immediate:** Create `research/alpha_miner/` directory structure
2. **Week 1:** Implement Phase 1 (Expression Tree & Generator)
3. **Week 2:** Implement Phase 2-3 (GA & PCA)
4. **Week 3:** Implement Phase 4-5 (DNN & Factory)
5. **Week 4:** Implement Phase 6-8 (Risk, LLM, Testing)

---

*Document Version: 1.0*
*Created: 2026-09-09*
*Status: PLANNING*
