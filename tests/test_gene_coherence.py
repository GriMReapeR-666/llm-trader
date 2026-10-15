"""Gene coherence: structural invariants the GA must not violate.

`repair_gene` is the trust boundary for three untrusted inputs:
  1. blended crossover (stop and target averaged independently can invert)
  2. random mutation (per-parameter clamps cannot see between-parameter order)
  3. LLM-proposed seeds (a model emits out-of-range values without complaint)
"""
from __future__ import annotations

import random

import pytest

from core.evolutionary_optimizer import (
    PARAM_SPACE,
    StrategyGene,
    crossover,
    llm_seed_gene,
    mutate,
    random_gene,
    repair_gene,
    semantic_crossover,
)


def coherent(g: StrategyGene) -> bool:
    """Every invariant that makes a gene tradable."""
    if not all(PARAM_SPACE[p][0] <= getattr(g, p) <= PARAM_SPACE[p][1] for p in PARAM_SPACE):
        return False
    if not g.take_profit_atr > g.stop_loss_atr:
        return False
    if g.partial_close_r > (g.take_profit_atr / g.stop_loss_atr) * 1.0001:
        return False
    if g.trailing_stop_activate_r > g.partial_close_r + 1e-9:
        return False
    if not g.alpha_weights or abs(sum(g.alpha_weights.values()) - 1.0) > 1e-6:
        return False
    return all(w > 0 for w in g.alpha_weights.values())


# --- repair_gene ------------------------------------------------------------

def test_repair_restores_inverted_payoff():
    g = StrategyGene(gene_id="x", stop_loss_atr=3.0, take_profit_atr=0.5)
    repair_gene(g)
    assert g.take_profit_atr > g.stop_loss_atr
    assert coherent(g)


def test_repair_enforces_partial_close_within_target_r():
    g = StrategyGene(gene_id="x", stop_loss_atr=1.0, take_profit_atr=1.2,
                     partial_close_r=4.0, trailing_stop_activate_r=4.0)
    repair_gene(g)
    rr = g.take_profit_atr / g.stop_loss_atr
    assert g.partial_close_r <= rr
    assert g.trailing_stop_activate_r <= g.partial_close_r
    assert coherent(g)


def test_repair_drops_nan_negative_and_unknown_weights():
    g = StrategyGene(
        gene_id="x",
        alpha_weights={"alpha_001": float("nan"), "alpha_999": -3.0, "alpha_005": 0.5},
    )
    repair_gene(g)
    assert "alpha_999" not in g.alpha_weights          # not a known alpha id
    assert all(v > 0 and v == v for v in g.alpha_weights.values())
    assert abs(sum(g.alpha_weights.values()) - 1.0) < 1e-6


def test_repair_clamps_out_of_range_and_nonfinite_scalars():
    g = StrategyGene(gene_id="x", stop_loss_atr=float("inf"), risk_per_trade=50.0,
                     max_positions=-4, holding_period_bars=99999)
    repair_gene(g)
    assert coherent(g)
    assert g.max_positions >= 1
    assert g.holding_period_bars <= PARAM_SPACE["holding_period_bars"][1]


def test_repair_is_idempotent():
    g = random_gene("r", 0)
    repair_gene(g)
    snapshot = g.to_dict()
    repair_gene(g)
    assert g.to_dict() == snapshot


def test_repair_returns_same_object():
    g = random_gene("r", 0)
    assert repair_gene(g) is g


# --- operators keep the invariant -------------------------------------------

def test_semantic_crossover_always_coherent():
    random.seed(7)
    for _ in range(3000):
        a, b = random_gene("a"), random_gene("b")
        assert coherent(semantic_crossover(a, b, "c", 1))


def test_mutate_always_coherent():
    random.seed(11)
    for _ in range(3000):
        assert coherent(mutate(random_gene("m"), 0.2, 0.15))


def test_legacy_crossover_can_break_coherence():
    """Guards the premise: if this stops failing, the repair is redundant.

    Averaging stop and target independently CAN invert the payoff. The observed
    rate is low (~0.1%) because take_profit_atr's range sits mostly above
    stop_loss_atr's, so this is latent rather than chronic -- but it is real,
    and it would become chronic if either range moved.
    """
    random.seed(7)
    broke = 0
    for _ in range(4000):
        a, b = random_gene("a"), random_gene("b")
        if not coherent(crossover(a, b, "c", 1)):
            broke += 1
    assert broke > 0, "no longer reproducible -- review whether repair is still needed"


def test_semantic_crossover_preserves_blocks_whole():
    """A block inherited from a coherent parent arrives unmodified.

    Parents are repaired first: if an inherited block was already incoherent,
    ``repair_gene`` legitimately rewrites it afterwards, so exact equality only
    holds when the source block was sound to begin with.
    """
    random.seed(5)
    checked = 0
    for _ in range(500):
        a, b = repair_gene(random_gene("a")), repair_gene(random_gene("b"))
        child = semantic_crossover(a, b, "c", 1)
        from_a = ((child.stop_loss_atr, child.take_profit_atr,
                   child.partial_close_r, child.trailing_stop_activate_r)
                  == (a.stop_loss_atr, a.take_profit_atr,
                      a.partial_close_r, a.trailing_stop_activate_r))
        from_b = ((child.stop_loss_atr, child.take_profit_atr,
                   child.partial_close_r, child.trailing_stop_activate_r)
                  == (b.stop_loss_atr, b.take_profit_atr,
                      b.partial_close_r, b.trailing_stop_activate_r))
        assert from_a or from_b, "risk_reward block was blended, not inherited whole"
        checked += 1
    assert checked == 500


def test_semantic_crossover_resets_measured_fields():
    """Fitness is a measurement; inheriting it would fake an evaluation."""
    a, b = random_gene("a"), random_gene("b")
    a.fitness, a.sharpe, a.n_trades = 2.0, 3.0, 99
    b.fitness, b.sharpe, b.n_trades = 1.0, 2.0, 50
    child = semantic_crossover(a, b, "c", 1)
    assert (child.fitness, child.sharpe, child.n_trades) == (0.0, 0.0, 0)


def test_semantic_crossover_prefers_the_fitter_parent():
    random.seed(3)
    elite, weak = random_gene("elite"), random_gene("weak")
    elite.fitness, weak.fitness = 1.5, 0.1
    from_elite = 0
    n = 4000
    for _ in range(n):
        child = semantic_crossover(elite, weak, "c", 1)
        if (child.stop_loss_atr, child.take_profit_atr) == (elite.stop_loss_atr,
                                                            elite.take_profit_atr):
            from_elite += 1
    assert from_elite / n > 0.55, "no selection pressure toward the better parent"


def test_semantic_crossover_records_lineage():
    a, b = random_gene("pa"), random_gene("pb")
    child = semantic_crossover(a, b, "child", 4)
    assert child.parent_ids == ["pa", "pb"]
    assert child.generation == 4
    assert child.gene_id == "child"


# --- LLM seeding ------------------------------------------------------------

class _DeadRouter:
    agents: dict = {}
    providers: dict = {}

    def generate(self, *a, **k):
        raise RuntimeError("no network")


class _StubRouter:
    def __init__(self, reply):
        self.reply = reply
        self.agents = {"evolutionary_optimizer": {}}
        self.providers = {"openrouter": {}}
        self.last_prompt = None

    def generate(self, agent, messages, **k):
        self.last_prompt = messages[-1]["content"]
        return self.reply


def test_llm_seed_falls_back_when_router_is_dead():
    res = llm_seed_gene(router=_DeadRouter())
    assert res["source"] == "random_fallback"
    assert res["execution_authority"] is False
    assert coherent(res["gene"])


def test_llm_seed_falls_back_on_unparseable_reply():
    res = llm_seed_gene(router=_StubRouter("I think you should use a wider stop."))
    assert res["source"] == "random_fallback"
    assert res["reason"] == "no_json_in_response"
    assert coherent(res["gene"])


def test_llm_seed_falls_back_on_empty_reply():
    res = llm_seed_gene(router=_StubRouter(""))
    assert res["source"] == "random_fallback"
    assert coherent(res["gene"])


def test_llm_seed_falls_back_when_no_known_fields():
    res = llm_seed_gene(router=_StubRouter('{"colour": "green"}'))
    assert res["source"] == "random_fallback"
    assert res["reason"] == "no_usable_fields"


def test_llm_seed_repairs_hostile_but_usable_reply():
    reply = (
        '{"stop_loss_atr": 2.5, "take_profit_atr": 0.1, "risk_per_trade": 9.0,'
        ' "max_positions": -3, "alpha_weights": {"alpha_001": -1, "alpha_005": 4}}'
    )
    res = llm_seed_gene(router=_StubRouter(reply))
    assert res["source"] == "llm"
    assert res["execution_authority"] is False
    g = res["gene"]
    # the model said what it wanted; repair overrode it
    assert g.take_profit_atr > g.stop_loss_atr
    assert g.risk_per_trade <= PARAM_SPACE["risk_per_trade"][1]
    assert g.max_positions >= 1
    assert "alpha_001" not in g.alpha_weights
    assert coherent(g)


def test_llm_seed_accepts_a_well_formed_reply():
    reply = (
        '{"stop_loss_atr": 1.2, "take_profit_atr": 3.0, "risk_per_trade": 0.008,'
        ' "max_positions": 2, "holding_period_bars": 24,'
        ' "alpha_weights": {"alpha_001": 0.6, "alpha_005": 0.4}}'
    )
    res = llm_seed_gene(router=_StubRouter(reply))
    assert res["source"] == "llm"
    g = res["gene"]
    assert (g.stop_loss_atr, g.take_profit_atr) == (1.2, 3.0)
    assert g.holding_period_bars == 24
    assert coherent(g)


def test_llm_seed_prompt_states_the_hard_invariants():
    router = _StubRouter("{}")
    llm_seed_gene(router=router, elites=[random_gene("e", 0)])
    assert "MUST be greater than stop_loss_atr" in router.last_prompt
    assert "alpha_001" in router.last_prompt


@pytest.mark.parametrize("value", [None, "nan", float("inf")])
def test_repair_survives_hostile_scalar_types(value):
    g = StrategyGene(gene_id="x", stop_loss_atr=value, take_profit_atr=value)
    repair_gene(g)
    assert coherent(g)
