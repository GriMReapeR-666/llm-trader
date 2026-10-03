# Quant Knowledge Framework

**Isolated framework for batch-downloading YouTube transcriptions and organizing quantitative finance knowledge from YouTube channels, books, and other sources.**

## Overview

This framework is completely isolated from the main trading system. It:
- Batch-fetches YouTube subtitles/transcriptions **without downloading video/audio**
- Parses subtitles into plain-text transcripts
- Classifies content into 5 quant topics (+ archive)
- Builds a searchable, organized knowledge base
- Provides agent-executable quant modules (13 tools / 11 modules)

## Important

This framework is configured for **transcription-only** mode by default. It does not download video or audio files. It only fetches available subtitles and auto-generated captions via `yt-dlp`, then converts them to searchable text.

## Quick Start

```bash
# Install in editable mode
pip install -e .

# Run full pipeline
quant-knowledge build --config config/sources.yaml

# Or run individual stages
quant-knowledge download --channel "https://www.youtube.com/@UnbiasedTrading"
quant-knowledge transcribe --input data/raw_downloads
quant-knowledge classify --input data/transcriptions
```

> **Note:** in `transcript_only` mode the downloader already parses each video's
> subtitle into `<video_dir>/transcript.txt`. The transcribe stage reuses those
> files as-is (copying them to `data/transcriptions/<channel>_<video_id>.txt`),
> parses only subtitles that lack a fresh transcript, and only loads a whisper
> model when real audio/video files are present. Re-runs are idempotent.
> With `--input`, pass the same directory the downloader writes to
> (`data/raw_downloads`), not a directory of loose audio files.

## Smoke test

```bash
# from quant_knowledge_framework/
python smoke_test.py            # pytest + E2E battery + pipeline probes
python smoke_test.py --verbose  # also print full probe output inline
python smoke_test.py --fast     # sandbox probes only (~2s); launch preflight
```

`--fast` skips the pytest suite and E2E battery and runs only the sandboxed
pipeline probes, so it is safe to run on a launch path. Both `START_DECK.bat`
and `START_TRADING.bat` honor an opt-in `SMOKE_PREFLIGHT=1` environment
variable: the preflight runs `smoke_test.py --fast` and aborts the launch on a
RED verdict (default is off, so normal launches are unchanged).

Exits 0 (GREEN) only when all steps pass (pytest suite, E2E battery, and the
pipeline probes); writes a marker-footed report to
`../artifacts/qkf_smoke_report.txt`. Also available from the agent shell:
`exec knowledge.smoke_test`.

## CLI

```text
quant-knowledge download   --channel <url> --limit <n>
quant-knowledge transcribe --input <dir>
quant-knowledge classify   --input <dir>
quant-knowledge search     <query>
quant-knowledge stats
quant-knowledge reclassify
quant-knowledge build      --channels <ch1> <ch2>
quant-knowledge agent
```

## Topics

| ID | Topic |
|:---|:---|
| 01 | Regime Detection |
| 02 | Strategy Development |
| 03 | Portfolio Management |
| 04 | Order Execution |
| 05 | Quant Utilities |
| 99 | Archive / Unsorted |

## Agent Interface

```python
from quant_framework.agent import QuantAgent

agent = QuantAgent()

# Discover available tools (19: 6 knowledge/pipeline + 13 quant module tools)
tools = agent.discover_tools()

# Knowledge tools
result = agent.execute_tool("knowledge.search", query="volatility")
result = agent.execute_tool("knowledge.classify", text="hidden markov model for regime detection")
result = agent.execute_tool("knowledge.get_content", path="data/knowledge_base/01_Regime_Detection/...")

# Quant module tools (bridged from the module registry; auto-populated on import)
result = agent.execute_tool("regime_detection.hurst.calculate", prices=[100, 101, 102, 103, 105])
result = agent.execute_tool("portfolio_management.kelly.calculate", win_rate=0.6, win_loss_ratio=1.5)
result = agent.execute_tool("strategy_development.dsr.calculate", sharpe_ratio=0.3, n=252, trials=100)

# Chain tools (module tools chain exactly like knowledge tools)
results = agent.chain_tools([
    {"tool": "portfolio_management.kelly.calculate", "params": {"win_rate": 0.6, "win_loss_ratio": 1.5}, "save_as": "kelly"},
    {"tool": "knowledge.search", "params": {"query": "kelly"}}
])
```

Note: `QuantAgent(config_path)` resolves `config/sources.yaml` relative to the
current working directory (package root when running from there). Module tools
work with any config path, including none at all.

## Quant Modules

All modules auto-register in a global registry on
`import quant_framework.modules` (each is instantiated once at import; the
registry is idempotent). Every tool below is agent-callable.

| Module | Tool | What it does |
|:---|:---|:---|
| `regime_detection.hmm` | `.fit` / `.classify` | Gaussian HMM over returns (± vol); states labeled by volatility rank (`calm`/`normal`/`high_vol`); classify auto-fits on first use |
| `regime_detection.hurst` | `.calculate` | Hurst exponent via lag-difference scaling: `H` = log-log slope of `std(log_P[t+lag] − log_P[t])` vs lag; confidence = regression R². Trending H>0.55, mean-reverting H<0.45 |
| `strategy_development.wfo` | `.optimize` | Walk-forward efficiency (avg OOS Sharpe / avg IS Sharpe), `robust = wfe >= 0.6` |
| `strategy_development.dsr` | `.calculate` | **Bailey/López de Prado 2014** Deflated Sharpe Ratio: probability that true SR exceeds the expected max SR of `trials` variants; PSR denominator corrects for skew/kurtosis; significant at DSR > 0.95 |
| `strategy_development.triple_barrier` | `.label` | Triple-barrier labels (TP +1 / SL −1 / time 0) |
| `portfolio_management.kelly` | `.calculate` | Kelly f*, fractional sizing, and Thorp (2006) drawdown risk: `P(ever below x·W₀) = x^(2/f−1)`; reports `prob_ever_half` |
| `portfolio_management.hrp` | `.optimize` | **López de Prado 2016** HRP: correlation distance → single-linkage clustering → seriation → recursive bisection (inverse-variance cluster allocation) |
| `order_execution.spread` | `.calculate` | Dynamic spread: base × (1 + 0.75·(volRatio−1) + 0.5·(liqRatio−1)) × session |
| `order_execution.almgren_chriss` | `.calculate` | Permanent + temporary market impact, bps |
| `quant_utilities.expectancy` | `.calculate` / `.r_multiple` | Trade expectancy (EV per R); R-multiple with `dollar_profit` = absolute per-unit P&L (exit − entry) |
| `quant_utilities.drawdown` | `.analyze` | Max drawdown, longest underwater run, Calmar, CAGR |

## Knowledge Base

- `data/knowledge_base/` — classified transcripts (950 files across 6 topics)
- `data/knowledge_base/index.json` — rebuilt by `quant-knowledge build`
- `data/raw_downloads/` — per-source download folders with per-video metadata

Search uses a tokenized inverted index persisted as
`data/knowledge_base/search_index.json` next to `index.json`: tokens are
prefix-matched ("volat" finds "volatility"), multi-token queries AND their
terms, and the index incrementally syncs itself against the KB directory
(mtime+size fingerprinting) whenever it is used or rebuilt.

## Tests

```bash
python -m pytest tests -q
```

The suite includes `tests/test_numeric_correctness.py`, which pins hand-derived
values for every module tool (e.g. Hurst H>0.55 for trending AR(1), HRP
[¼,¼,½] for a duplicated-asset pair, Kelly f* = ⅓, DSR cross-checked against an
independent scipy implementation) so math regressions fail loudly.
