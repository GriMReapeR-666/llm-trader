# Dispersion Bias — Research Dossier

**Folder:** `docs/dispersion_bias/`  
**Primary paper:** Goldberg, Papanicolaou, Shkolnik — *The Dispersion Bias* (arXiv:1711.05360v4, 2017-2018, SIAM J. Financial Math)  
**Secondary:** Analyst dispersion anomaly literature (JSTOR / ScienceDirect 2025 review)  
**Scope:** Extract every system/theorem/tool from the paper, evaluate usefulness for this repo (LLM trader → `core/`, `hierarchical_portfolio`, `risk_manager`, `portfolio_heat`, `cpcv`, `live_advisory_hub`), and document only what passes the usefulness gate.

```
docs/dispersion_bias/
├── README.md                      # this file — map
├── PAPER_SUMMARY.md               # full paper dissection (sections, assumptions, results)
├── SYSTEMS_CATALOG.md             # all systems extracted (formal description + inputs/outputs)
├── USEFULNESS_EVALUATION.md       # per-system: useful? HFT relevance? cost/benefit → GO/NO-GO
└── IMPLEMENTATION_BLUEPRINT.md    # for GO systems: spec, pseudocode, integration points, tests
```

**One-liner verdict:** One system is **HIGH usefulness, low cost** — eigenvector dispersion correction for the dominant factor. Implement it as an **advisory → risk-gate** overlay on `hierarchical_portfolio` and `TradingStore` heat. The rest is NO-GO for live loop (research/PROMOTION gate only).

**Quick links:**
* Paper PDF: https://arxiv.org/pdf/1711.05360
* arXiv landing: https://arxiv.org/abs/1711.05360
* ESCHOLARSHIP mirror: https://escholarship.org/content/qt4kt5g2x3/qt4kt5g2x3.pdf
* Analyst dispersion anomaly: https://www.sciencedirect.com/science/article/pii/S0378426625001979

