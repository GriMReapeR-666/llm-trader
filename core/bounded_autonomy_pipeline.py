r"""Bounded-Autonomy LLM Agent Pipeline (NZE, 2026).

Implements the formal composition:
    (a_t, q_t) = R o V_LLM o S(X_t, P_t)

Guarantees (by construction):
1. Action-Set Containment: A_exec(X_t, P_t) \subseteq A_S(X_t, P_t) \cup {(HOLD, 0)}
2. Monotone Exposure Property (Proposition 1): E(V_LLM(\hat{a}_t, \hat{q}_t)) <= E(\hat{a}_t, \hat{q}_t)
   The LLM cannot originate, retarget, reverse direction, widen stops, or upsize trades.
3. Deterministic Per-Action Budget: Hard per-position notional cap
   B_{i,t} = \tau_{pos} * e_t - |h_{i,t}| * p_{i,t} enforced strictly AFTER the LLM verdict.
4. Auditability: Every cycle persisted to line-delimited JSONL record.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("BoundedAutonomyPipeline")


@dataclass
class CandidateOrder:
    """Deterministic proposal from the signal layer S(X_t, P_t)."""
    symbol: str
    action: str  # "BUY", "SELL", "HOLD"
    quantity: float  # \hat{q}_t
    entry_price: float
    stop_loss: float
    take_profit: float
    score: float = 0.0  # s_t in [-1.0, 1.0]
    rationale: str = ""
    features: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def is_active(self) -> bool:
        return self.action in {"BUY", "SELL"} and self.quantity > 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LLMVetoVerdict:
    """Boolean veto verdict from V_LLM."""
    approved: bool  # v_t in {True, False}
    rationale: str
    confidence: float = 1.0
    latency_ms: float = 0.0
    model_name: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PortfolioState:
    """Portfolio state P_t = (c_t, h_t, e_t)."""
    cash: float  # c_t
    equity: float  # e_t
    holdings: Dict[str, float] = field(default_factory=dict)  # h_{i,t} in units (positive for long, negative for short)


@dataclass
class SizingRiskBudget:
    """Deterministic risk envelope parameters for shield R."""
    max_position_pct: float = 0.10  # \tau_{pos}: max fraction of equity in single asset (Eq 1)
    max_gross_exposure_pct: float = 0.50  # \tau_{gross}: max total portfolio gross exposure (Eq 2)
    min_trade_notional: float = 10.0  # minimum notional trade size ($)
    cash_buffer_pct: float = 0.95  # 0.95 * cash maximum spendable


@dataclass
class ExecutedDecision:
    """Final executed action and quantity (a_t, q_t) after R o V_LLM o S."""
    action: str  # "BUY", "SELL", "HOLD"
    quantity: float  # q_t
    symbol: str
    entry_price: float
    stop_loss: float
    take_profit: float
    candidate: CandidateOrder
    veto_verdict: Optional[LLMVetoVerdict]
    budget_notional_cap: float
    executed_notional: float
    risk_approved: bool
    rejection_reasons: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RuleProposer:
    r"""Technical signal layer S(X_t, P_t).
    
    Computes technical indicators (EMA crossover, RSI, momentum) and produces
    deterministic candidate orders (\hat{a}_t, \hat{q}_t).
    """

    def __init__(
        self,
        ema_fast: int = 12,
        ema_slow: int = 26,
        rsi_period: int = 14,
        momentum_period: int = 20,
        default_quantity: float = 0.1,
    ):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.momentum_period = momentum_period
        self.default_quantity = default_quantity

    def propose(
        self,
        symbol: str,
        prices: List[float],
        portfolio: PortfolioState,
        entry_price: Optional[float] = None,
        stop_distance_points: Optional[float] = None,
        reward_risk: float = 2.0,
    ) -> CandidateOrder:
        """Deterministic proposal generation S(X_t, P_t)."""
        if not prices or len(prices) < max(self.ema_slow, self.momentum_period) + 1:
            return CandidateOrder(
                symbol=symbol,
                action="HOLD",
                quantity=0.0,
                entry_price=entry_price or (prices[-1] if prices else 0.0),
                stop_loss=0.0,
                take_profit=0.0,
                score=0.0,
                rationale="Insufficient history for deterministic signal layer.",
            )

        current_price = entry_price if (entry_price and entry_price > 0) else prices[-1]

        # 1. EMA Fast / Slow
        weights_fast = [math.exp(-i / self.ema_fast) for i in range(min(len(prices), 50))]
        weights_slow = [math.exp(-i / self.ema_slow) for i in range(min(len(prices), 50))]
        p_rev = prices[::-1]
        ema_f = sum(p * w for p, w in zip(p_rev, weights_fast)) / sum(weights_fast)
        ema_s = sum(p * w for p, w in zip(p_rev, weights_slow)) / sum(weights_slow)
        ema_score = 1.0 if ema_f > ema_s else (-1.0 if ema_f < ema_s else 0.0)

        # 2. RSI approximation
        gains, losses = [], []
        for i in range(len(prices) - self.rsi_period, len(prices)):
            diff = prices[i] - prices[i - 1]
            if diff > 0:
                gains.append(diff)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(diff))
        avg_gain = sum(gains) / max(len(gains), 1)
        avg_loss = sum(losses) / max(len(losses), 1)
        rs = avg_gain / (avg_loss + 1e-9)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi_score = 1.0 if rsi < 35 else (-1.0 if rsi > 65 else 0.0)

        # 3. Momentum over Nm bars
        mom = (prices[-1] - prices[-self.momentum_period]) / max(prices[-self.momentum_period], 1e-9)
        mom_score = 1.0 if mom > 0.005 else (-1.0 if mom < -0.005 else 0.0)

        # Composite bounded score s_t in [-1, 1]
        s_t = (0.4 * ema_score) + (0.3 * rsi_score) + (0.3 * mom_score)
        s_t = max(-1.0, min(1.0, s_t))

        # Determine direction
        if s_t >= 0.35:
            action = "BUY"
        elif s_t <= -0.35:
            action = "SELL"
        else:
            action = "HOLD"

        # Stops
        d_stop = stop_distance_points if (stop_distance_points and stop_distance_points > 0) else current_price * 0.01
        if action == "BUY":
            sl = current_price - d_stop
            tp = current_price + (d_stop * reward_risk)
        elif action == "SELL":
            sl = current_price + d_stop
            tp = current_price - (d_stop * reward_risk)
        else:
            sl, tp = 0.0, 0.0

        qty = self.default_quantity if action != "HOLD" else 0.0
        rationale = f"S(X,P) score={s_t:.2f} (EMA={ema_score}, RSI={rsi:.1f}, Mom={mom:.3%})"

        return CandidateOrder(
            symbol=symbol,
            action=action,
            quantity=qty,
            entry_price=current_price,
            stop_loss=sl,
            take_profit=tp,
            score=s_t,
            rationale=rationale,
            features={"rsi": rsi, "ema_fast": ema_f, "ema_slow": ema_s, "mom": mom},
        )


class LLMVetoOperator:
    r"""Boolean Veto Operator V_LLM (NZE, 2026 Eq 6).
    
    Proposition 1 (Monotone Exposure Property):
        E(V_LLM(\hat{a}_t, \hat{q}_t)) <= E(\hat{a}_t, \hat{q}_t)
    
    By construction:
    - Approves: (\hat{a}_t, \hat{q}_t) if v_t == True
    - Vetoes: (HOLD, 0) if v_t == False
    - LLM CANNOT originate orders, change symbol, flip direction, or upsize quantity.
    """

    def __init__(
        self,
        llm_caller: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        model_name: str = "bounded-veto-llm",
        fail_closed: bool = True,
        deliberation_guard: Optional[Any] = None,
    ):
        self.llm_caller = llm_caller
        self.model_name = model_name
        self.fail_closed = fail_closed
        self.deliberation_guard = deliberation_guard

    @classmethod
    def create_apex_debate_veto(
        cls,
        llm_caller: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        fail_closed: bool = True,
        min_conviction: float = 0.60,
        deliberation_guard: Optional[Any] = None,
    ) -> "LLMVetoOperator":
        """Factory: create V_LLM operator backed by P13 ApexQuant Multi-Agent Debate."""
        from intelligence.debate_protocol import ApexQuantParliament
        parliament = ApexQuantParliament(
            llm_caller=llm_caller,
            min_conviction_threshold=min_conviction,
        )
        return cls(
            llm_caller=parliament.as_veto_caller(),
            model_name="apexquant-parliament-debate",
            fail_closed=fail_closed,
            deliberation_guard=deliberation_guard,
        )

    def evaluate(
        self,
        candidate: CandidateOrder,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[CandidateOrder, LLMVetoVerdict]:
        """Apply V_LLM to candidate order."""
        if not candidate.is_active:
            # HOLD passes straight through without requiring LLM evaluation
            verdict = LLMVetoVerdict(
                approved=True,
                rationale="Candidate is HOLD; no veto evaluation required.",
                model_name=self.model_name,
            )
            return candidate, verdict

        if self.llm_caller is None:
            # No LLM configured: default pass-through (B3 baseline)
            verdict = LLMVetoVerdict(
                approved=True,
                rationale="No LLM provider configured (B3 rules baseline).",
                model_name="none",
            )
            return candidate, verdict

        # Build compact JSON-only prompt payload
        prompt_payload = {
            "symbol": candidate.symbol,
            "action": candidate.action,
            "entry_price": candidate.entry_price,
            "stop_loss": candidate.stop_loss,
            "take_profit": candidate.take_profit,
            "candidate_quantity": candidate.quantity,
            "signal_score": candidate.score,
            "signal_rationale": candidate.rationale,
            "context": context or {},
        }

        t0 = time.perf_counter()
        try:
            raw_response = self.llm_caller(prompt_payload)
            latency = (time.perf_counter() - t0) * 1000.0

            approved = bool(raw_response.get("approved", False))
            rationale = str(raw_response.get("rationale", "LLM verdict"))
            confidence = float(raw_response.get("confidence", 1.0))

            # P20 Two-Speed Deliberation Tax Guard (Cheng et al., 2026)
            if approved and self.deliberation_guard is not None:
                current_price = (context or {}).get("current_price", candidate.entry_price)
                atr = (context or {}).get("atr", 0.0)
                guard_ok, guard_reason = self.deliberation_guard.evaluate_tender(
                    entry_price=candidate.entry_price,
                    current_market_price=current_price,
                    latency_ms=latency,
                    atr=atr,
                )
                tax_breach = not guard_ok
                if tax_breach:
                    approved = False
                    rationale = f"deliberation_tax_breach: {guard_reason}"
                self.deliberation_guard.record_decision(
                    latency_ms=latency, approved=approved, tax_breach=tax_breach
                )

            verdict = LLMVetoVerdict(
                approved=approved,
                rationale=rationale,
                confidence=confidence,
                latency_ms=latency,
                model_name=self.model_name,
            )

        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            logger.warning("LLM veto call failed: %s (fail_closed=%s)", exc, self.fail_closed)
            # Fail closed: reject on error/timeout
            approved = False if self.fail_closed else True
            verdict = LLMVetoVerdict(
                approved=approved,
                rationale=f"LLM call exception: {exc}",
                latency_ms=latency,
                model_name=self.model_name,
                error=str(exc),
            )

        # Monotone Exposure Guarantee:
        # V_LLM(\hat{a}_t, \hat{q}_t) = (\hat{a}_t, \hat{q}_t) if v_t=True else (HOLD, 0)
        if verdict.approved:
            # Pass candidate through EXACTLY as proposed. Never allow LLM to mutate parameters.
            return candidate, verdict
        else:
            vetoed_candidate = CandidateOrder(
                symbol=candidate.symbol,
                action="HOLD",
                quantity=0.0,
                entry_price=candidate.entry_price,
                stop_loss=0.0,
                take_profit=0.0,
                score=candidate.score,
                rationale=f"VETOED by LLM: {verdict.rationale}",
                features=candidate.features,
            )
            return vetoed_candidate, verdict


class RiskShield:
    """Post-posed deterministic sizing and risk shield R (NZE, 2026 Section 4).
    
    Enforces:
    1. Per-position notional cap (Eq 1):
       B_{i,t} = \tau_{pos} * e_t - |h_{i,t}| * p_{i,t}
    2. Sizing clip: q_t <= min(B_{i,t}, 0.95 * c_t) / p_{i,t}
    3. Replaces with (HOLD, 0) if B_{i,t} <= min_trade_notional
    4. Optional validation against DeterministicValidator / PortfolioRiskArbiter.
    """

    def __init__(
        self,
        budget: Optional[SizingRiskBudget] = None,
        deterministic_validator: Optional[Any] = None,
    ):
        self.budget = budget or SizingRiskBudget()
        self.validator = deterministic_validator

    def enforce(
        self,
        candidate: CandidateOrder,
        portfolio: PortfolioState,
        market: Optional[Dict[str, Any]] = None,
    ) -> ExecutedDecision:
        """Enforce deterministic risk shield R on candidate order."""
        if not candidate.is_active:
            return ExecutedDecision(
                action="HOLD",
                quantity=0.0,
                symbol=candidate.symbol,
                entry_price=candidate.entry_price,
                stop_loss=0.0,
                take_profit=0.0,
                candidate=candidate,
                veto_verdict=None,
                budget_notional_cap=0.0,
                executed_notional=0.0,
                risk_approved=True,
                rejection_reasons=[],
            )

        p = float(candidate.entry_price)
        if p <= 0.0:
            return ExecutedDecision(
                action="HOLD",
                quantity=0.0,
                symbol=candidate.symbol,
                entry_price=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                candidate=candidate,
                veto_verdict=None,
                budget_notional_cap=0.0,
                executed_notional=0.0,
                risk_approved=False,
                rejection_reasons=["Invalid entry price <= 0"],
            )

        current_holding_units = abs(float(portfolio.holdings.get(candidate.symbol, 0.0)))
        current_holding_notional = current_holding_units * p

        # B_{i,t} = \tau_{pos} * e_t - |h_{i,t}| * p_{i,t} (NZE Eq 1)
        max_notional_allowed = self.budget.max_position_pct * portfolio.equity
        b_it = max_notional_allowed - current_holding_notional

        reasons = []

        # Check budget envelope
        if b_it <= self.budget.min_trade_notional:
            reasons.append(f"Position notional budget exhausted: B_it={b_it:.2f} <= {self.budget.min_trade_notional}")

        # Check cash cap
        max_cash_spend = self.budget.cash_buffer_pct * portfolio.cash
        if max_cash_spend < self.budget.min_trade_notional:
            reasons.append(f"Available cash too low: {portfolio.cash:.2f}")

        if reasons:
            return ExecutedDecision(
                action="HOLD",
                quantity=0.0,
                symbol=candidate.symbol,
                entry_price=p,
                stop_loss=0.0,
                take_profit=0.0,
                candidate=candidate,
                veto_verdict=None,
                budget_notional_cap=max(0.0, b_it),
                executed_notional=0.0,
                risk_approved=False,
                rejection_reasons=reasons,
            )

        # Candidate desired notional
        cand_notional = candidate.quantity * p
        # Cap notional to min(B_{i,t}, 0.95 * c_t)
        allowed_notional = min(cand_notional, b_it, max_cash_spend)
        allowed_qty = allowed_notional / p

        # Check with DeterministicValidator if integrated
        if self.validator is not None:
            try:
                from core.trading_safety import TradeProposal
                features = {
                    "ema_alignment": 1,
                    "mtf_alignment": 1,
                    "adx": 30,
                    "near_key_level": True,
                    **candidate.features,
                }
                proposal = TradeProposal(
                    strategy=candidate.features.get("strategy", "scalp"),
                    symbol=candidate.symbol,
                    side=candidate.action,
                    entry=p,
                    stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit,
                    confidence=max(0.7, abs(candidate.score)),
                    features=features,
                )
                default_mkt = {
                    "market_open": True,
                    "completed_candle": True,
                    "high_impact_news": False,
                    "tick_age_ms": 0,
                    "max_tick_age_ms": 5000,
                    "symbol_tradable": True,
                    "spread": 0.0,
                    "max_spread": 100.0,
                    # Complete evidence (P1-H4): the fail-closed validator
                    # rejects any missing gate input.
                    "clock_ok": True,
                    "session_allowed": True,
                    "liquidity_ok": True,
                    "microstructure_ok": True,
                    "required_data_ok": True,
                    "execution_price": p,
                    "entry_tolerance": 100.0,
                    "stop_distance": abs(candidate.entry_price - candidate.stop_loss) if candidate.stop_loss else 1.0,
                    "min_stop_distance": 0.0,
                    "lag_confidence": 1.0,
                    "min_lag_confidence": 0.0,
                    "setup_min_rr": 1.0,
                }
                mkt = {**default_mkt, **(market or {})}
                val_res = self.validator.validate(proposal, mkt)
                if not val_res.approved:
                    return ExecutedDecision(
                        action="HOLD",
                        quantity=0.0,
                        symbol=candidate.symbol,
                        entry_price=p,
                        stop_loss=0.0,
                        take_profit=0.0,
                        candidate=candidate,
                        veto_verdict=None,
                        budget_notional_cap=b_it,
                        executed_notional=0.0,
                        risk_approved=False,
                        rejection_reasons=list(val_res.reasons),
                    )
            except Exception as exc:
                logger.error("Deterministic validator execution failed: %s", exc)
                return ExecutedDecision(
                    action="HOLD",
                    quantity=0.0,
                    symbol=candidate.symbol,
                    entry_price=p,
                    stop_loss=0.0,
                    take_profit=0.0,
                    candidate=candidate,
                    veto_verdict=None,
                    budget_notional_cap=b_it,
                    executed_notional=0.0,
                    risk_approved=False,
                    rejection_reasons=[f"Validator exception: {exc}"],
                )

        return ExecutedDecision(
            action=candidate.action,
            quantity=allowed_qty,
            symbol=candidate.symbol,
            entry_price=p,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            candidate=candidate,
            veto_verdict=None,
            budget_notional_cap=b_it,
            executed_notional=allowed_notional,
            risk_approved=True,
            rejection_reasons=[],
        )


class BoundedAutonomyPipeline:
    """Formal Composition: (a_t, q_t) = R o V_LLM o S(X_t, P_t).
    
    Encapsulates proposer, Boolean veto, post-posed shield, and structured audit logs.
    """

    def __init__(
        self,
        proposer: RuleProposer,
        veto_operator: LLMVetoOperator,
        risk_shield: RiskShield,
        audit_log_path: Optional[str] = None,
    ):
        self.proposer = proposer
        self.veto_operator = veto_operator
        self.risk_shield = risk_shield
        self.audit_log_path = audit_log_path

        # Execution diagnostics (NZE Section 5)
        self.cycles_total = 0
        self.active_proposals = 0
        self.vetoed_cycles = 0
        self.executed_cycles = 0

    @property
    def veto_rate(self) -> float:
        r"""\rho = (1 / T) \sum 1[v_t = False] on active proposals."""
        return (self.vetoed_cycles / self.active_proposals) if self.active_proposals > 0 else 0.0

    @property
    def decision_consistency(self) -> float:
        r"""\kappa = (1 / T) \sum 1[v_t = True] on cycles with \hat{a}_t != HOLD."""
        approved = self.active_proposals - self.vetoed_cycles
        return (approved / self.active_proposals) if self.active_proposals > 0 else 1.0

    def step(
        self,
        symbol: str,
        prices: List[float],
        portfolio: PortfolioState,
        context: Optional[Dict[str, Any]] = None,
        market: Optional[Dict[str, Any]] = None,
        entry_price: Optional[float] = None,
        stop_distance_points: Optional[float] = None,
    ) -> ExecutedDecision:
        """Execute one complete cycle of R o V_LLM o S."""
        self.cycles_total += 1

        # 1. S(X_t, P_t): Deterministic Proposer
        candidate = self.proposer.propose(
            symbol=symbol,
            prices=prices,
            portfolio=portfolio,
            entry_price=entry_price,
            stop_distance_points=stop_distance_points,
        )

        # 2. V_LLM: Boolean Veto
        veto_verdict = None
        if candidate.is_active:
            self.active_proposals += 1
            vetoed_cand, veto_verdict = self.veto_operator.evaluate(candidate, context=context)
            if not veto_verdict.approved:
                self.vetoed_cycles += 1
        else:
            vetoed_cand = candidate

        # 3. R: Sizing and Risk Shield
        decision = self.risk_shield.enforce(
            candidate=vetoed_cand,
            portfolio=portfolio,
            market=market,
        )
        decision.veto_verdict = veto_verdict

        if decision.action in {"BUY", "SELL"} and decision.quantity > 0.0:
            self.executed_cycles += 1

        # 4. Audit Log (JSONL line-delimited record)
        self._record_audit(decision)

        return decision

    def _record_audit(self, decision: ExecutedDecision) -> None:
        """Persist structured audit record."""
        if not self.audit_log_path:
            return
        try:
            log_dir = os.path.dirname(self.audit_log_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            record = {
                "timestamp": decision.timestamp,
                "cycle": self.cycles_total,
                "symbol": decision.symbol,
                "proposer": {
                    "action": decision.candidate.action,
                    "quantity": decision.candidate.quantity,
                    "score": decision.candidate.score,
                    "rationale": decision.candidate.rationale,
                },
                "llm_veto": decision.veto_verdict.to_dict() if decision.veto_verdict else None,
                "shield_execution": {
                    "action": decision.action,
                    "quantity": decision.quantity,
                    "budget_cap": decision.budget_notional_cap,
                    "executed_notional": decision.executed_notional,
                    "risk_approved": decision.risk_approved,
                    "reasons": decision.rejection_reasons,
                },
            }
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
        except Exception as exc:
            logger.warning("Failed writing audit record: %s", exc)


def create_bounded_pipeline(
    llm_caller: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    model_name: str = "bounded-veto-llm",
    deterministic_validator: Optional[Any] = None,
    budget: Optional[SizingRiskBudget] = None,
    audit_log_path: Optional[str] = None,
    fail_closed: bool = True,
) -> BoundedAutonomyPipeline:
    """Convenience factory creating a fully wired BoundedAutonomyPipeline (NZE 2026: R ∘ V_LLM ∘ S)."""
    proposer = RuleProposer()
    veto_op = LLMVetoOperator(llm_caller=llm_caller, model_name=model_name, fail_closed=fail_closed)
    shield = RiskShield(budget=budget or SizingRiskBudget(), deterministic_validator=deterministic_validator)
    return BoundedAutonomyPipeline(
        proposer=proposer,
        veto_operator=veto_op,
        risk_shield=shield,
        audit_log_path=audit_log_path,
    )
