# Quant Radio The Price Impact of Nothing

Topic: 05_Quant_Utilities
Confidence: 0.60
Keywords: pandas, numpy, python

---

## Quant Radio: The Price Impact of Nothing

**Source**: https://www.youtube.com/watch?v=fY_c2UPGJ6A  
**Video ID**: `fY_c2UPGJ6A`  
**Category**: Causal Inference

### Key Concepts

Causal portfolio optimization moves beyond correlation to identify true causal relationships.

### Code Module

```python
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, LogisticRegression

def causal_effect_estimate(treatment, outcome, confounders):
    X = np.column_stack([treatment, confounders])
    model = LinearRegression()
    model.fit(X, outcome)
    return model.coef_[0]

def propensity_score_matching(treatment, outcome, confounders):
    psmodel = LogisticRegression()
    psmodel.fit(confounders, treatment)
    propensity = psmodel.predict_proba(confounders)[:, 1]
    treated = propensity[treatment == 1]
    control = propensity[treatment == 0]
    effects = []
    for p in treated:
        nearest = control[np.argmin(np.abs(control - p))]
        effects.append(outcome[treatment == 1].iloc[0] - outcome[treatment == 0].iloc[0])
    return np.mean(effects)
```

### Explanation

Regression adjustment and propensity score matching for causal effect estimation.

---