# Autonomous Quant Trading Graph v2
### A Survivability-First Redesign — Gap Analysis and Remediation

---

## 0. Reframing "guarantee performance"

Before the design: no architecture guarantees returns. Say otherwise and you're either lying or you haven't shipped yet. Knight Capital had a fully automated execution system with no kill switch — it lost $440M in 45 minutes from a single bad deploy in 2012. LTCM had two Nobel laureates on the model and still blew up in 1998. Sophistication of the alpha engine is not what determines survival.

What *is* buildable and worth guaranteeing:

1. **The system will never lose more than a pre-committed amount before something outside its own control shuts it off.**
2. **Every claimed edge has survived a statistically honest test that accounts for how many times you looked, not just a backtest that happened to fit.**
3. **Compounding is a consequence of not blowing up, not a consequence of finding better alpha.** A 50% drawdown needs a 100% gain just to get back to even — the risk layer is what makes compounding mathematically possible at all.

That's the real target. Everything below is built around it. v1 (the document you shared) is a reasonable *topology* for this, but nearly every "quality" number in it (`sharpe`, `overfitting_score`, `decay_score`) is a placeholder standing in for the actual hard part. This doc fills those in.

---

## 1. The central flaw in v1: it's a hypothesis mill with no multiple-testing correction

v1's Loop A runs the Idea Agent nightly, generating and backtesting hypotheses continuously, with a hardcoded gate: `Sharpe < 0.8 → reject`. This looks rigorous. It isn't.

If an autonomous agent tests 50 hypothesis variants a night, that's **18,250 backtests a year**. At that trial count, a fixed Sharpe threshold is nearly meaningless — pure noise clears it constantly. This is the single most common way autonomous strategy-discovery systems fool their own operators.

I ran the actual math using the **Deflated Sharpe Ratio** (Bailey & Author-C, 2014) — it corrects an observed Sharpe for the number of independent trials that produced it:

| Trials attempted | Observed Sharpe | Deflated Sharpe Ratio (confidence true Sharpe > 0) |
|---|---|---|
| 1 | 1.20 | ~100% |
| 100 | 1.20 | ~100% |
| 1,000 | 1.20 | 84.8% |
| **18,250** (v1's implied trial rate) | **1.20** | **0.03%** |

Same backtest. Same Sharpe. The only thing that changed is how many times the system searched before finding it. At the trial volume v1's Loop A implies, a 1.2 Sharpe is statistically indistinguishable from noise. **This is not a minor gap — it inverts the meaning of every number the graph produces**, and it's exactly the failure mode an autonomous, always-searching system is most exposed to (a human researcher testing 3 ideas a week never hits this regime; an agent testing 50 a night hits it in three weeks).

The fix is a **Trial Ledger**: a persistent, never-resettable counter of every hypothesis ever tested, used to deflate every Sharpe claim going forward. No agent — including the "self-improving" Meta-Agent — gets to reset it.

---

## 2. Gap analysis: what v1 has vs. what's actually required

| v1 Component | What it does | Critical gap | Remediation |
|---|---|---|---|
| Idea Agent | Crawls SSRN/news, generates hypotheses | No trial accounting; crawled text treated as trusted input (prompt-injection surface) | **Research Governance Agent**: wraps Idea Agent, enforces Trial Ledger, sanitizes all scraped content as untrusted data, never as instructions |
| Data Agent | Fetches/cleans data | No survivorship-bias-free universe, no point-in-time fundamentals, no delisted tickers, no split/dividend adjustment pipeline | **Data Integrity Agent**: PIT data contracts, includes delisted names in the historical universe, versioned/checksummed datasets |
| Signal Agent | Generates features/code | No leakage control specified | Purged & embargoed cross-validation (Author-C) is *mandatory*, not optional — embargo period between train/test to kill autocorrelation leakage |
| Backtest Agent | Runs simulation, computes Sharpe/DD | `Sharpe < 0.8` is an arbitrary, trial-count-blind threshold; no cost/impact modeling in the sim itself | **Statistical Validation Agent** (new, sits after Backtest, before Risk): Deflated Sharpe Ratio + Probability of Backtest Overfitting via Combinatorially Purged CV |
| Risk Agent | VaR check | VaR assumes near-normal returns — fails exactly when it matters (fat tails, crisis correlation spikes) | **Portfolio Risk Agent** (upgraded): CVaR/Expected Shortfall, a stress-test library (2008, 2020, 2022 + synthetic tail scenarios), fractional-Kelly sizing, hard leverage/concentration caps |
| — (missing) | — | No allocation logic across multiple *concurrently live* strategies | **Capital Allocation Agent**: correlation-aware risk budgeting, CPPI-style drawdown-triggered de-risking |
| Execution Agent | Places orders | No slippage/market-impact model, no idempotency, no partial-fill handling, no broker reconciliation | Realistic execution simulation (square-root impact law) in backtest; idempotent order IDs + reconciliation loop live |
| — (missing) | — | The graph is the only thing that can stop the graph. A bug, a hallucination, or the exact failure mode that caused the loss can also disable the safety rail | **Guardian Process** (deliberately *not* an LLM agent — see §4) |
| — (missing) | — | LLM-generated code goes straight into the strategy with no independent check | **Code Safety Agent**: sandboxed execution, static analysis, diff review; anything touching *order-placement logic itself* (not signal logic) requires human sign-off at every autonomy level |
| — (missing) | — | No audit trail, no regulatory awareness | **Compliance & Audit Agent**: immutable append-only log of every state transition and LLM call; pattern-day-trading and wash-sale tracking; self-check that order-slicing logic doesn't produce spoofing-like patterns |
| Monitor Agent | "Statistical process control" (unspecified) | Decay detection with no defined test | CUSUM / Page-Hinkley change-point detection on live-vs-backtest Sharpe, with a real significance test, not a ratio threshold |
| Meta-Agent | Rewrites Idea Agent's prompts based on decay patterns | This is the hypothesis mill's problem one level up — untested "discovered patterns" get baked into future search, compounding overfit | Meta-Agent's discoveries must clear the *same* Statistical Validation gate before they're allowed to modify the Idea Agent's prompt |

---

## 3. The Guardian Process — why the kill switch cannot be part of the agentic graph

This is the single highest-leverage change from v1.

v1's Risk Agent is "just another node" — it reads graph state, decides, and if it's wrong (buggy prompt, hallucinated tool call, or the same regime shift that's causing the loss also confusing the risk logic), there's nothing outside the graph to catch it. Knight Capital's algorithm had risk controls *inside* the same deploy that broke — that's precisely why nothing stopped it.

**Principle: the thing enforcing a safety boundary must not be built from the same material you're trying to bound.** Real broker-dealers are required to have pre-trade risk controls independent of the trading algorithm itself (SEC Rule 15c3-5) for exactly this reason.

The Guardian Process is a separate, boring, deterministic daemon:

- Runs as its own process, outside the LangGraph runtime.
- Polls the **broker's account API directly** — never the agentic graph's own state store. If the graph is compromised, hung, or lying to itself, this must still see the truth.
- Thresholds are **hardcoded constants**, not LLM-decided, not configurable by any agent at runtime.
- Contains **zero LLM calls** in its execution path.
- Fails *closed*: any error talking to the broker is treated as a breach, not ignored.

```python
import time

# Hardcoded. Not read from graph state. Not LLM-decided.
MAX_DAILY_LOSS_PCT   = 0.05     # flatten at -5% of day-start equity
MAX_GROSS_LEVERAGE   = 2.0
HEARTBEAT_TIMEOUT_SEC = 120     # graph goes silent this long -> assume the worst
POLL_INTERVAL_SEC    = 15

def get_account_state():
    # Direct call to the BROKER. Never to the agentic graph.
    resp = requests.get(f"{BROKER_API_BASE}/v2/account", headers=AUTH_HEADERS, timeout=5)
    resp.raise_for_status()
    return resp.json()

def flatten_everything():
    requests.delete(f"{BROKER_API_BASE}/v2/orders", headers=AUTH_HEADERS, timeout=5)
    requests.delete(f"{BROKER_API_BASE}/v2/positions", headers=AUTH_HEADERS, timeout=5)
    page_human("KILL SWITCH FIRED — positions flattened, orders cancelled.")

def guardian_loop():
    day_start_equity = None
    last_graph_heartbeat = time.time()
    while True:
        try:
            account = get_account_state()
            equity = float(account["equity"])
            if day_start_equity is None or is_new_trading_day():
                day_start_equity = equity

            daily_pnl_pct = (equity - day_start_equity) / day_start_equity
            gross_leverage = (float(account["long_market_value"])
                               + abs(float(account["short_market_value"]))) / equity

            if daily_pnl_pct <= -MAX_DAILY_LOSS_PCT:
                flatten_everything(); halt_trading_for_the_day()
            if gross_leverage > MAX_GROSS_LEVERAGE:
                flatten_everything()
            if time.time() - last_graph_heartbeat > HEARTBEAT_TIMEOUT_SEC:
                flatten_everything()   # graph went dark -> don't trust it

        except Exception as e:
            # Fail CLOSED: if we can't confirm the account is safe, treat it as a breach.
            page_human(f"Guardian error, flattening as precaution: {e}")
            flatten_everything()

        time.sleep(POLL_INTERVAL_SEC)
```

This runs before the graph ever touches live capital — including paper trading with a real API key, since key leakage and infra bugs are just as real in paper mode.

---

## 4. Statistical Validation Agent (the new mandatory gate)

Sits between Backtest and Risk. Nothing reaches the Risk/Capital Allocation stage without clearing this.

```python
def statistical_validation_gate(state):
    trial_ledger.increment()  # persistent, never reset by any agent

    dsr = deflated_sharpe_ratio(
        observed_sharpe=state["sharpe"],
        n_trials=trial_ledger.count,
        skewness=state["returns_skew"],
        kurtosis=state["returns_kurtosis"],
        n_obs=state["n_periods"],
        variance_of_trial_sharpes=trial_ledger.variance_of_all_trials(),
    )
    pbo = combinatorial_purged_cv_pbo(state["signal_series"], state["price_data"])

    if dsr < 0.95:
        return "fail_not_significant"   # looks good, probably just lucky given how many trials
    if pbo > 0.5:
        return "fail_overfit"           # rank flips out-of-sample across CV splits -> overfit
    if state["max_dd"] > MAX_ALLOWED_DD:
        return "fail_drawdown"
    return "pass"
```

`combinatorial_purged_cv_pbo` implements Bailey & Author-C's **Probability of Backtest Overfitting**: split the backtest into N groups, form every combinatorial train/test partition, and measure how often the in-sample-best configuration ranks poorly out-of-sample. A strategy that only "works" in one specific train/test split is a strategy that doesn't work.

---

## 5. Portfolio Risk Agent (upgraded)

VaR alone is the wrong tool — it's a normal-distribution-shaped hammer applied to fat-tailed, correlation-spiking markets, and it fails silently in exactly the conditions you built it for.

- **CVaR / Expected Shortfall** instead of (or alongside) VaR — answers "if we're in the bad 5%, how bad on average," not just "where's the 5% line."
- **Stress-test library**: replay 2008, 2020 (COVID), 2022 (rates) against the *current* book, plus synthetic scenarios (correlation → 1 across the whole book, a 3x volatility shock, a liquidity freeze on the least-liquid position).
- **Fractional Kelly sizing** with a hard leverage ceiling — full Kelly is provably too aggressive under model uncertainty; most live systems run at 0.25–0.5x Kelly.
- **Concentration limits** — hard caps per instrument, per sector, per strategy, independent of what the sizing formula outputs.

## 6. Capital Allocation Agent (missing from v1 entirely)

v1 has no logic for what happens when three strategies are live simultaneously. Needed:

- Risk-budget allocation across live strategies, weighted by realized (not backtested) correlation to the rest of the book.
- CPPI-style de-risking: as portfolio drawdown deepens, allocated capital shrinks nonlinearly — this is what actually prevents ruin, more than any single strategy's edge.
- Strategy retirement rule: a strategy whose live Sharpe falls persistently below its Statistical-Validation-Agent-approved expectation loses allocation *before* the Monitor Agent even flags decay — allocation should react faster than the retrain cycle.

## 7. Code Safety Agent (missing from v1 entirely)

The Signal Agent in v1 generates Python and runs it. For an LLM-authored, self-modifying trading system, this is the most dangerous single line in the whole document.

- All generated code runs in a sandboxed container first — no network, no broker credentials, resource-limited.
- Static analysis + property-based tests (not just unit tests against one fixture).
- Every change is diffed against the previous version and logged.
- **Hard rule, no autonomy-level exception**: code that touches order-placement logic (as opposed to signal/feature logic) requires human review before it ships. Signal research can run unattended; the part that talks to your broker cannot.

## 8. Compliance & Audit Agent (missing from v1 entirely)

- Immutable, append-only log of every state transition and every LLM call (prompt + response + token cost) — you need this to reconstruct "why did it do that" after the fact, not just a P&L number.
- Pattern-day-trading and wash-sale tracking for tax/regulatory purposes.
- A basic self-check on order-slicing logic: rapid cancel/replace patterns generated by naive TWAP logic can look like spoofing to an exchange's surveillance systems even with zero manipulative intent — worth catching before a regulator does.

---

## 9. Updated state object

```python
from dataclasses import dataclass, field
from typing import Literal, Optional, Dict

@dataclass
class QuantGraphStateV2:
    run_id: str

    # --- new: statistical honesty ---
    trial_count: int = 0
    dsr: float = 0.0                    # Deflated Sharpe Ratio
    pbo: float = 1.0                    # Probability of Backtest Overfitting

    # --- new: real risk ---
    cvar_95: float = 0.0
    stress_test_results: Dict[str, float] = field(default_factory=dict)
    kelly_fraction: float = 0.0

    # --- new: portfolio-level ---
    capital_allocated: float = 0.0
    correlation_to_book: float = 0.0

    # --- new: safety, read-only from the graph's perspective ---
    kill_switch_status: Literal["armed", "tripped"] = "armed"
    audit_hash: Optional[str] = None
    code_review_status: Literal["pending", "approved", "rejected"] = "pending"
```

`kill_switch_status` is deliberately **read-only** from inside the graph — the Guardian Process writes to it; no agent node is permitted to.

---

## 10. Updated topology

```
                                    ┌───────────────────────────────┐
                                    │   GUARDIAN PROCESS (outside)   │
                                    │  no LLM · hardcoded thresholds │
                                    │  polls BROKER directly          │
                                    └────────────┬────────────────────┘
                                                 │ can flatten/halt at any time
┌────────────────────────────────────────────────▼──────────────────────────────┐
│                              ORCHESTRATOR (LangGraph)                          │
└───────────────┬─────────────────────────────────────────────────┬────────────┘
                │                                                   │
                ▼                                                   ▼
        ┌──────────────┐                                   ┌────────────────┐
        │ RESEARCH      │  (wraps Idea Agent,               │ COMPLIANCE &    │
        │ GOVERNANCE    │   enforces Trial Ledger,          │ AUDIT AGENT     │◄── logs every
        │ AGENT         │   sanitizes scraped input)        │ (append-only)   │    transition
        └──────┬────────┘                                   └────────────────┘
               ▼
        ┌──────────────┐        ┌──────────────┐
        │ DATA          │──────►│ SIGNAL AGENT  │
        │ INTEGRITY     │        │ (purged CV    │
        │ AGENT (PIT)   │        │  mandatory)   │
        └──────────────┘        └──────┬────────┘
                                        ▼
                                 ┌──────────────┐
                                 │ BACKTEST      │
                                 │ AGENT         │
                                 │ (cost/impact  │
                                 │  modeled)     │
                                 └──────┬────────┘
                                        ▼
                            ┌────────────────────────┐   FAIL   ┌─────────────┐
                            │ STATISTICAL VALIDATION  │─────────►│ back to     │
                            │ AGENT (DSR + PBO gate)  │          │ Idea Agent  │
                            └────────────┬─────────────┘          └─────────────┘
                                    PASS │
                                        ▼
                                 ┌───────────────┐
                                 │ CODE SAFETY    │  order-logic changes
                                 │ AGENT           │─────────────────► human sign-off
                                 └──────┬─────────┘                    (always)
                                        ▼
                            ┌────────────────────┐
                            │ PORTFOLIO RISK       │  (CVaR, stress tests,
                            │ AGENT                │   fractional Kelly)
                            └──────────┬───────────┘
                                       ▼
                            ┌────────────────────┐
                            │ CAPITAL ALLOCATION   │  (correlation-aware,
                            │ AGENT                │   CPPI de-risking)
                            └──────────┬───────────┘
                                       ▼
                            ┌────────────────────┐
                            │ EXECUTION AGENT      │◄──── idempotent order IDs,
                            │                      │      broker reconciliation
                            └──────────┬───────────┘
                                       ▼
                            ┌────────────────────┐
                            │ MONITOR AGENT        │  CUSUM / Page-Hinkley
                            │                      │  change-point detection
                            └──────────┬───────────┘
                                       │ decay detected, statistically confirmed
                                       ▼
                            ┌────────────────────┐
                            │ META-AGENT            │  proposed pattern must
                            │ (constrained)         │──itself clear the same──► Statistical
                            └────────────────────┘   Validation gate           Validation Agent
```

---

## 11. Revised levels of autonomy

v1's L0–L3 ladder treats "autonomy" as one dial. It's really two independent axes — **research autonomy** (finding and testing ideas) and **execution autonomy** (deploying capital) — and they should not advance at the same pace.

| Level | Research autonomy | Execution autonomy |
|---|---|---|
| L0 | Human writes hypotheses | Human clicks every order |
| L1 | Agents generate/backtest ideas; human approves before Statistical Validation runs | Agents size positions; human approves before any live order |
| L2 | Full research loop unattended, gated by Statistical Validation Agent | Automated execution within Guardian Process limits; human reviews weekly |
| L3 | Full research loop + Meta-Agent prompt tuning, gated | Automated execution, Guardian Process is the only human-independent stop |

Order-placement *code changes* require human sign-off at every level, including L3 — that's not a maturity gate, it's a permanent control, for the same reason airline autopilot doesn't get to rewrite its own flight-control software mid-flight.

---

## 12. Build order (unglamorous things first)

Building the flashy "self-improving" loop first is exactly backwards — it's the highest-overfitting-risk component, so it should be the *last* thing turned on, once everything underneath it is proven stable.

1. **Data Integrity Agent** + point-in-time data warehouse. Boring. Most consequential. Nothing above this layer means anything if the data has survivorship bias or look-ahead leakage.
2. **Guardian Process**, live and tested with a *fake* account before any real capital — including before paper trading with a real broker API key, since credential and infra failures are real in paper mode too.
3. **Statistical Validation Agent** (DSR + PBO), running against a small, fixed, human-written set of hypotheses first — prove the gate rejects noise before letting an agent feed it at volume.
4. **Backtest engine with realistic costs**, calibrated against known historical fills if you have any live trading history at all.
5. **Idea Agent automation** — now that everything downstream is trustworthy, let it run unattended.
6. **Meta-Agent self-improvement loop** — last, and every "discovered pattern" still has to clear step 3's gate before it's allowed to change future behavior.

---

**The honest summary:** v1 is good scaffolding wearing the vocabulary of a real quant system without the actual statistical and safety machinery underneath. The fix isn't more agents — it's putting a small number of specific, boring, non-negotiable gates (trial-corrected significance, an off-graph kill switch, human sign-off on order logic) in the path of the parts that are otherwise happy to lie to you convincingly.
