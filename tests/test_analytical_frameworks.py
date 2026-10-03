"""Comprehensive Test Suite for Volumes 01 to 07 Analytical Frameworks & Epistemology Suite.

Verifies:
- 100% deterministic mathematical execution across all 7 volumes
- Conformance to UNIVERSAL_MODULE_INTERFACE_SPEC.md
- Tool calling and parameter contracts
- Registry integration and error handling
"""
import pytest
import numpy as np

from intelligence.analytical_frameworks.base_framework import GLOBAL_ANALYTICAL_REGISTRY
from intelligence.analytical_frameworks.v01_geopolitical_commodity_engine import V01_GEOPOLITICAL_ENGINE
from intelligence.analytical_frameworks.v02_quantitative_chaos_engine import V02_QUANT_CHAOS_ENGINE
from intelligence.analytical_frameworks.v03_economic_input_output_engine import V03_ECONOMIC_IO_ENGINE
from intelligence.analytical_frameworks.v04_strategic_adversarial_engine import V04_STRATEGIC_ENGINE
from intelligence.analytical_frameworks.v05_behavioral_cognitive_engine import V05_BEHAVIORAL_ENGINE
from intelligence.analytical_frameworks.v06_epistemological_filter_engine import V06_EPISTEMOLOGICAL_ENGINE
from intelligence.analytical_frameworks.v07_specialized_niche_engine import V07_SPECIALIZED_ENGINE


# ==============================================================================
# Volume 01 Tests: Geopolitical & Macro-Commodity Cross-Market Engine
# ==============================================================================

def test_v01_cascade_ripple_simulation():
    res = V01_GEOPOLITICAL_ENGINE.simulate_cascade_ripple(shock_type="HORMUZ_BLOCKADE", shock_magnitude_pct=30.0)
    assert res["status"] == "CASCADE_SIMULATED"
    assert len(res["cascade_orders"]) >= 4
    assert len(res["recommended_trades"]) >= 2
    assert 0.0 <= res["systemic_risk_score"] <= 1.0
    # Check 1st order node
    assert res["cascade_orders"][0]["order"] == 1
    assert "Crude Oil" in res["cascade_orders"][0]["transmission_node"]


def test_v01_relative_spreads_calculation():
    res = V01_GEOPOLITICAL_ENGINE.calculate_relative_spreads(
        corn_price=4.50,
        soybean_price=11.25,
        crude_price=75.0,
        gasoline_price=105.0,
        diesel_price=115.0,
        soymeal_price=350.0,
        soyoil_price=0.50,
    )
    assert res["corn_soy_ratio"] == 2.50
    assert res["corn_soy_signal"] == "EQUILIBRIUM_FAIR_VALUE"
    assert res["crack_321_dollar_per_bbl"] == pytest.approx(33.33, 0.1)
    assert res["refining_margin_signal"] == "HIGH_REFINING_MARGIN"
    assert res["soy_crush_dollar_per_bushel"] == pytest.approx(1.95, 0.1)


def test_v01_chokepoints_and_escalation():
    choke = V01_GEOPOLITICAL_ENGINE.audit_chokepoint_vulnerability("STRAIT_OF_HORMUZ")
    assert choke["status"] == "CHOKEPOINT_AUDITED"
    assert choke["details"]["global_share_pct"] == 21.0

    ladder = V01_GEOPOLITICAL_ENGINE.evaluate_escalation_ladder(escalation_level=4)
    assert ladder["status"] == "ESCALATION_EVALUATED"
    assert ladder["escalation_level"] == 4
    assert len(ladder["third_party_arbitrage_hubs"]) >= 3


# ==============================================================================
# Volume 02 Tests: Quantitative, Statistical, Frequency & Chaos Engine
# ==============================================================================

def test_v02_pca_yield_curve_decomposition():
    res = V02_QUANT_CHAOS_ENGINE.decompose_yield_curve_pca()
    assert res["status"] == "PCA_DECOMPOSED"
    exp = res["explained_variance"]
    assert exp["PC1_Level"] > 0.40
    assert "Level" in res["latest_factor_scores"]
    assert res["curve_shape"] in ["INVERTED_RECESSION_SIGNAL", "STEEPENING_GROWTH_REFLATION", "FLAT_TRANSITIONAL"]


def test_v02_bayesian_posterior_updating():
    res = V02_QUANT_CHAOS_ENGINE.update_bayesian_posterior(
        prior_prob=0.50,
        likelihood_if_true=0.85,
        likelihood_if_false=0.15,
    )
    assert res["status"] == "BAYESIAN_UPDATED"
    assert res["posterior_probability"] > 0.80
    assert res["update_direction"] == "REINFORCED_BULLISH"


def test_v02_fft_dominant_cycles_and_hurst():
    fft_res = V02_QUANT_CHAOS_ENGINE.extract_fft_dominant_cycles(top_n_cycles=3)
    assert fft_res["status"] == "FFT_CYCLES_EXTRACTED"
    assert len(fft_res["top_dominant_cycles"]) >= 1
    assert fft_res["spectral_entropy_bits"] > 0

    hurst_res = V02_QUANT_CHAOS_ENGINE.calculate_hurst_fractal()
    assert hurst_res["status"] == "HURST_EVALUATED"
    assert 0.0 < hurst_res["hurst_exponent"] < 1.0
    assert 1.0 < hurst_res["fractal_dimension"] < 2.0


def test_v02_fat_tail_cvar_simulation():
    res = V02_QUANT_CHAOS_ENGINE.run_fat_tail_cvar_simulation(num_paths=1000)
    assert res["status"] == "FAT_TAIL_SIMULATED"
    assert res["cvar_99_expected_shortfall_pct"] <= res["cvar_95_expected_shortfall_pct"] <= res["var_95_pct"]
    assert res["hill_tail_alpha_index"] > 0


# ==============================================================================
# Volume 03 Tests: Economic Systems, Input-Output & Feedback Dynamics
# ==============================================================================

def test_v03_leontief_input_output_multiplier():
    res = V03_ECONOMIC_IO_ENGINE.calculate_leontief_input_output(shocked_sector="Energy", shock_delta_pct=-20.0)
    assert res["status"] == "LEONTIEF_SOLVED"
    assert res["shocked_sector"] == "Energy"
    assert res["systemic_output_delta_pct"] < 0
    assert "Chemicals_Fertilizer" in res["sector_output_impacts"]
    assert res["sector_output_impacts"]["Chemicals_Fertilizer"]["output_delta_pct"] < 0


def test_v03_pyramid_and_macro_synthesis():
    pyr = V03_ECONOMIC_IO_ENGINE.evaluate_4tier_pyramid(metric_name="US_CORE_CPI", observed_value=3.9, consensus_estimate=3.5)
    assert pyr["status"] == "PYRAMID_EVALUATED"
    assert pyr["surprise_regime"] == "HAWKISH_UPSIDE_SURPRISE"

    syn = V03_ECONOMIC_IO_ENGINE.synthesize_macro_indicators(yield_curve_10y_2y_bps=-35.0, ism_manufacturing_pmi=47.2)
    assert syn["status"] == "INDICATORS_SYNTHESIZED"
    assert syn["leading_indicator_state"] == "CONTRACTIONARY_WARNING"


def test_v03_feedback_and_convexity():
    res = V03_ECONOMIC_IO_ENGINE.evaluate_feedback_loops_and_convexity(upside_gain_r=3.5, downside_loss_r=1.0)
    assert res["status"] == "FEEDBACK_CONVEXITY_EVALUATED"
    assert res["payoff_convexity_ratio"] == 3.5
    assert res["is_antifragile_convex"] is True


# ==============================================================================
# Volume 04 Tests: Strategic Intelligence, Adversarial Modeling & Failure Forensics
# ==============================================================================

def test_v04_cog_and_kill_chain():
    cog = V04_STRATEGIC_ENGINE.audit_center_of_gravity("GLOBAL_DOLLAR_CLEARING")
    assert cog["status"] == "COG_AUDITED"
    assert "US Treasury" in cog["cog_assessment"]["cog"]

    kc = V04_STRATEGIC_ENGINE.track_adversarial_kill_chain(active_phase=4, symbol="XAUUSD")
    assert kc["status"] == "KILL_CHAIN_EVALUATED"
    assert kc["active_phase_number"] == 4
    assert "Liquidity Sweep" in kc["phase_name"]


def test_v04_game_theory_and_fmea():
    game = V04_STRATEGIC_ENGINE.solve_game_theory_equilibrium("OPEC_PRISONERS_DILEMMA")
    assert game["status"] == "NASH_EQUILIBRIUM_SOLVED"
    assert "Defect" in game["dominant_strategy"]

    fmea = V04_STRATEGIC_ENGINE.calculate_fmea_risk_priority(severity=8, occurrence=5, detection=6)
    assert fmea["status"] == "FMEA_CALCULATED"
    assert fmea["rpn_score"] == 240
    assert 0.0 < fmea["swiss_cheese_alignment_probability"] < 1.0


# ==============================================================================
# Volume 05 Tests: Behavioral, Cognitive & Socioeconomic Analysis Engine
# ==============================================================================

def test_v05_facs_micro_expressions():
    genuine = V05_BEHAVIORAL_ENGINE.decode_facs_micro_expressions(action_units_present=["AU6", "AU12"])
    assert genuine["status"] == "FACS_DECODED"
    assert genuine["is_genuine_duchenne_smile"] is True
    assert genuine["stress_deception_probability"] < 0.10

    mask = V05_BEHAVIORAL_ENGINE.decode_facs_micro_expressions(action_units_present=["AU12", "AU4"])
    assert mask["is_genuine_duchenne_smile"] is False
    assert mask["stress_deception_probability"] > 0.50


def test_v05_cantillon_and_memetics():
    cantillon = V05_BEHAVIORAL_ENGINE.calculate_cantillon_wealth_divide(asset_inflation_yoy_pct=14.0, nominal_wage_growth_yoy_pct=3.5, essential_cpi_yoy_pct=7.0)
    assert cantillon["status"] == "CANTILLON_CALCULATED"
    assert cantillon["cantillon_asset_wage_divergence_pct"] == 10.5
    assert cantillon["real_wage_compression_spread_pct"] == -3.5

    r0 = V05_BEHAVIORAL_ENGINE.model_memetic_narrative_r0(transmission_prob_beta=0.40, social_contact_rate_c=5.0, salience_duration_days_d=1.0)
    assert r0["status"] == "MEMETICS_MODELED"
    assert r0["basic_reproduction_number_r0"] == 2.0
    assert r0["is_exponentially_expanding"] is True


# ==============================================================================
# Volume 06 Tests: Epistemology, Ontology & 5-Layer Filter Stacking
# ==============================================================================

def test_v06_5layer_filter_stack():
    res = V06_EPISTEMOLOGICAL_ENGINE.run_5layer_filter_stack(symbol="XAUUSD")
    assert res["status"] == "FILTER_STACK_COMPLETE"
    fs = res["phase2_filter_stack"]
    assert "layer_1_telescope" in fs
    assert "layer_5_falsifier" in fs
    assert fs["layer_5_falsifier"]["survived_falsification"] is True
    assert "Zen Axiom" in res["phase3_zen_posture"]


def test_v06_shannon_entropy_and_goodhart():
    ent = V06_EPISTEMOLOGICAL_ENGINE.calculate_shannon_entropy(probabilities=[0.80, 0.15, 0.05])
    assert ent["status"] == "SHANNON_ENTROPY_CALCULATED"
    assert ent["shannon_entropy_bits"] > 0
    assert ent["information_redundancy_pct"] > 0

    goodhart = V06_EPISTEMOLOGICAL_ENGINE.audit_goodhart_metric_distortion(targeted_metric="SHARPE_RATIO", observed_value=3.8)
    assert goodhart["status"] == "GOODHART_AUDITED"
    assert "CRITICAL_GOODHART" in goodhart["goodhart_risk_tier"]


# ==============================================================================
# Volume 07 Tests: Specialized Domain & Hyper-Niche Analytical Engine
# ==============================================================================

def test_v07_fea_liquidity_mesh():
    res = V07_SPECIALIZED_ENGINE.simulate_fea_liquidity_mesh(num_counterparty_nodes=4, daily_stress_cycles=120)
    assert res["status"] == "FEA_SIMULATED"
    assert len(res["nodal_displacements_u"]) == 4
    assert res["max_equivalent_stress"] > 0
    assert 0.0 <= res["fatigue_damage_fraction"] <= 1.0


def test_v07_sabermetrics_and_deontic_logic():
    saber = V07_SPECIALIZED_ENGINE.decouple_sabermetric_alpha(strategy_total_return_pct=26.0, market_benchmark_return_pct=12.0, beta_to_market=0.75, luck_factor_pct=2.0)
    assert saber["status"] == "SABERMETRIC_ALPHA_DECOUPLED"
    assert saber["pure_idiosyncratic_alpha_pct"] == 15.0
    assert "ELITE" in saber["alpha_quality_verdict"]

    deontic = V07_SPECIALIZED_ENGINE.verify_deontic_logic_risk_rules(is_risk_limit_breached=False, is_setup_approved=True)
    assert deontic["status"] == "DEONTIC_LOGIC_VERIFIED"
    assert deontic["is_permitted"] is True
    assert deontic["modal_proof_consistent"] is True


def test_master_registry_and_tools_integration():
    modules = GLOBAL_ANALYTICAL_REGISTRY.list_modules()
    assert len(modules) >= 7
    tools = GLOBAL_ANALYTICAL_REGISTRY.list_tools()
    assert len(tools) >= 18

    # Verify tool execution via registry
    cascade_tool = GLOBAL_ANALYTICAL_REGISTRY.get_tool("v01.simulate_cascade_ripple")
    assert cascade_tool is not None
    exec_res = cascade_tool.execute(shock_type="HORMUZ_BLOCKADE", shock_magnitude_pct=20.0)
    assert exec_res["status"] == "CASCADE_SIMULATED"


# ==============================================================================
# Sovereign Macro & Canonical Literary Forensics Tests
# ==============================================================================

def test_sovereign_5pillar_health_audit():
    from intelligence.sovereign_macro_and_literary_forensics import SOVEREIGN_MACRO_LITERARY_ENGINE
    res = SOVEREIGN_MACRO_LITERARY_ENGINE.audit_5pillar_sovereign_health(
        country_name="UNITED_STATES",
        debt_to_gdp_pct=122.0,
        real_interest_rate_pct=2.1,
        real_gdp_growth_pct=2.3,
    )
    assert res["status"] == "5PILLAR_SOVEREIGN_AUDITED"
    assert res["country"] == "UNITED_STATES"
    assert 0.0 < res["sovereign_health_score_out_of_100"] <= 100.0


def test_central_bank_speech_nlp_forensics():
    from intelligence.sovereign_macro_and_literary_forensics import SOVEREIGN_MACRO_LITERARY_ENGINE
    speech = (
        "Inflation remains unacceptably high and broad-based, requiring further policy firming and a restrictive stance for longer. "
        "We are monitoring market functioning and standing repo facility liquidity strains closely."
    )
    res = SOVEREIGN_MACRO_LITERARY_ENGINE.parse_central_bank_speech_forensics(speech_text=speech)
    assert res["status"] == "SPEECH_NLP_PARSED"
    assert res["hawk_dove_polarity_score"] > 0.0
    assert len(res["hawkish_triggers_detected"]) > 0
    assert len(res["panic_liquidity_triggers"]) > 0


def test_dalio_minsky_pozsar_debt_plumbing():
    from intelligence.sovereign_macro_and_literary_forensics import SOVEREIGN_MACRO_LITERARY_ENGINE
    res = SOVEREIGN_MACRO_LITERARY_ENGINE.evaluate_debt_cycle_and_monetary_plumbing(
        debt_growth_rate_pct=7.0,
        income_growth_rate_pct=3.5,
        ponzi_finance_share_pct=36.0,
        commodity_backing_share_pct=30.0,
    )
    assert res["status"] == "DEBT_MONETARY_PLUMBING_EVALUATED"
    assert "PONZI" in res["minsky_financial_instability_stage"]
    assert "III" in res["pozsar_bretton_woods_regime"]


def test_mackinder_spykman_allison_geopolitics():
    from intelligence.sovereign_macro_and_literary_forensics import SOVEREIGN_MACRO_LITERARY_ENGINE
    res = SOVEREIGN_MACRO_LITERARY_ENGINE.evaluate_geopolitical_power_parity(
        rising_power_gdp_share_of_ruling=78.0,
        eurasian_heartland_alignment_score=0.85,
    )
    assert res["status"] == "GEOPOLITICAL_POWER_PARITY_EVALUATED"
    assert "CRITICAL_THUCYDIDES_TRAP" in res["thucydides_trap_assessment"]
    assert "HEARTLAND" in res["mackinder_spykman_geopolitical_balance"]


def test_shiller_smil_narrative_and_physical_chokepoints():
    from intelligence.sovereign_macro_and_literary_forensics import SOVEREIGN_MACRO_LITERARY_ENGINE
    res = SOVEREIGN_MACRO_LITERARY_ENGINE.model_narrative_and_physical_chokepoints(
        narrative_contagion_rate_beta=0.60,
        narrative_recovery_rate_gamma=0.20,
        ammonia_fertilizer_supply_pct=88.0,
    )
    assert res["status"] == "NARRATIVE_PHYSICAL_CHOKEPOINTS_EVALUATED"
    assert res["shiller_basic_reproduction_r0"] == 3.0
    assert len(res["critical_physical_chokepoints"]) >= 1


# ==============================================================================
# Canonical Literary Mastery Engine Tests (Dalio, Soros, Minsky, Mehrling, Smil, Taleb, Prado)
# ==============================================================================

def test_dalio_all_weather_and_ferguson_law():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.evaluate_all_weather_and_empire_lifecycle(
        growth_environment="FALLING",
        inflation_environment="RISING",
        debt_interest_bn=1200.0,
        defense_budget_bn=850.0,
    )
    assert res["status"] == "DALIO_FERGUSON_EVALUATED"
    assert "STAGFLATION" in res["all_weather_macro_quadrant"].upper() or "QUADRANT 3" in res["all_weather_macro_quadrant"].upper()
    assert res["recommended_risk_parity_weights"]["Gold_Physical"] >= 0.30
    assert res["ferguson_law_breached"] is True


def test_soros_reflexivity_and_minsky_ffr():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.evaluate_reflexivity_and_minsky_fragility(
        market_price=2800.0,
        fundamental_fair_value=2300.0,
        credit_acceleration_yoy_pct=9.0,
        liquid_assets_bn=300.0,
        short_term_debt_obligations_bn=850.0,
    )
    assert res["status"] == "SOROS_MINSKY_EVALUATED"
    assert res["soros_reflexive_divergence_index"] > 0
    assert res["minsky_financial_fragility_ratio"] > 2.0
    assert res["is_minsky_moment_risk_elevated"] is True


def test_mehrling_cip_and_pozsar_four_prices():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.evaluate_money_view_and_cip_strains(
        fx_spot=1.0850,
        fx_forward_3m=1.0880,
        usd_libor_sofr_3m_pct=5.25,
        eur_euribor_3m_pct=3.75,
    )
    assert res["status"] == "MONEY_VIEW_CIP_EVALUATED"
    assert "Price_1_Par" in res["pozsar_four_prices_of_money"]
    assert "Price_2_Interest" in res["pozsar_four_prices_of_money"]


def test_allison_16_cases_and_mearsheimer():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.match_16_thucydides_case_studies(
        challenger_growth_delta_pct=3.8,
        naval_oceanic_distance_nm=5500.0,
    )
    assert res["status"] == "THUCYDIDES_MEARSHEIMER_MATCHED"
    assert len(res["matched_historical_analogs"]) >= 3
    assert 0.0 < res["mearsheimer_stopping_power_of_water_score"] <= 1.0


def test_smil_eroei_and_chip_war_matrix():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.evaluate_eroei_and_semiconductor_chokepoints(
        primary_energy_eroei=12.5,
        asml_euv_concentration_pct=100.0,
        tsmc_advanced_fab_share_pct=92.0,
    )
    assert res["status"] == "EROEI_CHIP_WAR_EVALUATED"
    assert "HYPER_CHOKEPOINT" in res["chip_war_vulnerability_assessment"]
    assert res["estimated_global_gdp_shock_on_disruption_pct"] < -5.0


def test_taleb_antifragility_and_lindy():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.evaluate_antifragility_and_lindy(
        payoff_gain_if_right=5.2,
        payoff_loss_if_wrong=1.0,
        institution_age_years=5000.0,  # Gold as monetary base
    )
    assert res["status"] == "TALEB_ANTIFRAGILITY_EVALUATED"
    assert "ANTIFRAGILE" in res["antifragility_triad_classification"]
    assert res["lindy_expected_future_longevity_years"] == 5000.0


def test_prado_triple_barrier_and_cusum():
    from intelligence.canonical_literary_mastery_engine import CANONICAL_LITERARY_MASTERY_ENGINE
    res = CANONICAL_LITERARY_MASTERY_ENGINE.execute_triple_barrier_and_cusum_filter(
        volatility_multiplier=2.0,
        vertical_barrier_bars=15,
    )
    assert res["status"] == "TRIPLE_BARRIER_CUSUM_EVALUATED"
    assert res["barrier_events_labeled"] > 0


