# Universal Quant Module Interface Specification

## Overview

This document defines the standardized interface that ALL quantitative finance modules must implement. This ensures any agent can discover, load, and execute modules consistently across all quant domains.

## Module Interface Contract

```yaml
module:
  id: string                    # Unique identifier: "regime_detection.hmm"
  name: string                  # Human-readable name
  version: string               # Semver: "1.0.0"
  category: enum                # regime_detection | strategy_development | portfolio_management | order_execution | quant_utilities
  tags: string[]               # Searchable tags: ["hmm", "regime", "volatility"]
  
  description: string           # What the module does
  references: string[]          # Source URLs, papers, books
  
  # Mathematical Foundation
  mathematics:
    formulas: string[]          # LaTeX formulas
    parameters: object          # Parameter definitions with defaults
    assumptions: string[]       # Model assumptions
    
  # Execution Interface
  execute:
    input_schema: object        # JSON Schema for inputs
    output_schema: object       # JSON Schema for outputs
    timeout_seconds: int        # Max execution time
    memory_mb: int              # Estimated memory footprint
    
  # Code Implementation
  implementation:
    language: string            # "python" | "rust" | "julia"
    entry_point: string         # Function/class name
    dependencies: string[]      # Required packages
    
  # Validation & Testing
  validation:
    test_cases: array           # Known input/output pairs
    benchmark_data: string      # Path to benchmark dataset
    
  # Agent Metadata
  agent:
    tools_provided: string[]    # Tool names this module exposes
    requires: string[]          # Other modules needed
    produces: string[]          # Data/products this module generates
```

## Tool/Function Calling Convention

All modules expose tools via this standardized format:

```python
@dataclass
class QuantTool:
    """Universal tool interface for quant modules."""
    name: str                    # Tool identifier: "regime_detection.hmm.classify"
    module_id: str               # Parent module ID
    description: str             # What the tool does
    parameters: Dict[str, Any]   # Input parameters with types/defaults
    returns: Dict[str, Any]      # Output schema
    
    def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool with given parameters."""
        raise NotImplementedError
```

## Agent Execution Protocol

```python
class QuantAgent:
    """Universal agent interface for quant operations."""
    
    def discover_tools(self, category: str = None) -> List[QuantTool]:
        """Discover available tools, optionally filtered by category."""
        pass
    
    def execute_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """Execute a tool by name with given parameters."""
        pass
    
    def chain_tools(self, pipeline: List[Dict]) -> Dict[str, Any]:
        """Execute a sequence of tools with data passing between them."""
        pass
```

## Example: Complete Module Definition

```yaml
module:
  id: "regime_detection.hmm"
  name: "Hidden Markov Model Regime Classifier"
  version: "1.0.0"
  category: "regime_detection"
  tags: ["hmm", "regime", "volatility", "states"]
  
  description: |
    Classifies market regime (trending, range-bound, high-volatility) 
    using a Gaussian Hidden Markov Model trained on returns and volatility.
    
  references:
    - "Hamilton, J.D. (1989). A New Approach to the Economic Analysis of Nonstationary Time Series."
    - "https://github.com/hmmlearn/hmmlearn"
    
  mathematics:
    formulas:
      - "P(S_t = j | S_{t-1} = i) = A_{ij}"
      - "P(O_t | S_t = j) = N(O_t; μ_j, Σ_j)"
    parameters:
      n_states:
        type: int
        default: 3
        description: "Number of hidden regimes"
      covariance_type:
        type: string
        default: "full"
        options: ["full", "diag", "tied"]
        
  execute:
    input_schema:
      type: object
      required: [returns]
      properties:
        returns:
          type: array
          description: "Time series of returns"
        vol:
          type: array
          description: "Optional volatility series"
    output_schema:
      type: object
      properties:
        regimes:
          type: array
          description: "Most likely regime sequence"
        probabilities:
          type: array
          description: "Regime probability matrix"
        current_regime:
          type: int
          description: "Current detected regime"
        confidence:
          type: float
          description: "Classification confidence"
          
  implementation:
    language: "python"
    entry_point: "HMMRegimeClassifier.classify"
    dependencies:
      - "numpy>=1.20"
      - "hmmlearn>=0.3.0"
      - "scipy>=1.7.0"
      
  validation:
    test_cases:
      - input: {"returns": [0.01, -0.02, 0.015, ...]}
        expected_output: {"current_regime": 0, "confidence": 0.85}
        
  agent:
    tools_provided:
      - "regime_detection.hmm.classify"
      - "regime_detection.hmm.predict"
      - "regime_detection.hmm.train"
    requires: []
    produces: ["regime_labels", "regime_probabilities"]
```
