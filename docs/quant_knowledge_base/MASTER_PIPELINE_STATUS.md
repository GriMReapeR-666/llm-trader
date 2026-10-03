# Quant Knowledge Base: Topic Overview

## Current Status

| Metric | Count |
| :--- | :--- |
| Total Source Files Indexed | 7 |
| Total Transcription Files | 0 (none found in project) |
| Total Code Snippets Extracted | 0 (pending transcription analysis) |

## What's Been Done

1. **Folder Structure Created** - 6 topic subfolders under `docs/quant_knowledge_base/`
2. **Source Survey Complete** - All existing quant documents in `docs/` have been catalogued
3. **No Existing Transcriptions Found** - The project contains research documents and code, but no downloaded video transcriptions

## What's Needed Next

To complete the knowledge base as requested:

### Option A: You Provide Video Sources
- Provide a list of specific YouTube video URLs or channel names you want transcribed
- Confirm you have rights to download/transcribe the content (or it's your own content)
- I can then:
  - Use `yt-dlp` to download audio/subtitles for specific videos
  - Run Whisper transcription on downloaded audio
  - Sort transcriptions into the topic folders
  - Extract code snippets and reusable functions

### Option B: Manual Ingestion
- You download videos/audio manually
- Place them in a staging folder
- I run the transcription and sorting pipeline

## Topic Classification Reference

| Topic | Keywords | Existing Sources |
| :--- | :--- | :--- |
| **01_Regime_Detection** | HMM, BOCPD, Hurst, ADX, ATR, volatility, market state | `retail quant model.txt`, `geopolitical_predictive_and_sanctions_intelligence/` |
| **02_Strategy_Development** | EV, backtesting, WFO, DSR, CPCV, signals, alpha | `QUANT_RESEARCH_AND_SYSTEM_FOUNDATIONS.md`, `retail_quant_master_architecture/` |
| **03_Portfolio_Management** | Kelly, HRP, risk parity, correlation, heat cap, allocation | `retail quant model.txt`, `retail_quant_master_architecture/` |
| **04_Order_Execution** | Almgren-Chriss, slippage, spread, market impact, TCA | `retail_quant_master_architecture/04_TRANSACTION_COST_ENGINE_AND_MARKET_IMPACT.md` |
| **05_Quant_Utilities** | Data pipelines, shared functions, feature engineering | `retail_quant_master_architecture/` |
| **99_Archive_Unsorted** | Content that doesn't fit above categories | - |

## Existing Code Snippets to Extract

From `retail_quant_master_architecture/`:
- `TransactionCostEngine` (Module 04) → 04_Order_Execution/code_snippets/
- `DynamicKalmanStatArb` (Module 05) → 01_Regime_Detection/code_snippets/ or 02_Strategy_Development/code_snippets/
- Mathematical reference tables (Module 01) → 05_Quant_Utilities/shared_functions/
