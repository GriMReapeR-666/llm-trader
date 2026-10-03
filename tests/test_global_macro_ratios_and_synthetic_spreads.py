"""Unit tests for Module 06: Universal Global Macro Ratios & Synthetic Spreads."""
import pytest

from intelligence.global_macro_ratios_and_synthetic_spreads import (
    calculate_copper_gold_ratio,
    calculate_copper_oil_ratio,
    calculate_lumber_gold_ratio,
    calculate_cyclicals_defensives_ratio,
    calculate_move_vix_ratio,
    calculate_three_two_one_crack_spread,
    calculate_brent_wti_transatlantic_spread,
    calculate_brent_dubai_efs_spread,
    calculate_gasoil_crack_spread,
    calculate_btu_energy_parity_ratio,
    calculate_clean_spark_spread,
    calculate_clean_dark_spread,
    calculate_haber_bosch_ammonia_cash_cost,
    calculate_corn_soybean_price_ratio,
    calculate_soybean_crush_spread,
    calculate_cattle_crush_feedlot_margin,
    calculate_hog_corn_ratio,
    calculate_wheat_corn_feed_spread,
    calculate_gold_silver_ratio,
    calculate_platinum_gold_ratio,
    calculate_steel_mill_hrc_spread,
    calculate_aluminum_smelter_margin,
    calculate_high_yield_investment_grade_oas_ratio,
    calculate_us_net_liquidity,
    evaluate_universal_macro_dashboard,
)


def test_growth_and_risk_ratios():
    # Copper $4.50/lb, Gold $2250/oz -> (4.50 / 2250) * 1000 = 2.00
    cu_au = calculate_copper_gold_ratio(copper_price_lb=4.50, gold_price_oz=2250.0)
    assert pytest.approx(cu_au, 1e-4) == 2.00

    # Copper $4.00/lb, WTI $80.00/bbl -> 4.00 / 0.80 = 5.0
    cu_oil = calculate_copper_oil_ratio(copper_price_lb=4.00, wti_crude_price_bbl=80.0)
    assert pytest.approx(cu_oil, 1e-4) == 5.0

    # Lumber $600, Gold $2000 -> 600 / 2000 = 0.30
    lumb_au = calculate_lumber_gold_ratio(lumber_price_1000_board_ft=600.0, gold_price_oz=2000.0)
    assert pytest.approx(lumb_au, 1e-4) == 0.30

    # XLI $140, XLU $70 -> 2.0
    xli_xlu = calculate_cyclicals_defensives_ratio(xli_industrials_price=140.0, xlu_utilities_price=70.0)
    assert pytest.approx(xli_xlu, 1e-4) == 2.0

    # MOVE 120, VIX 15 -> 8.0
    move_vix = calculate_move_vix_ratio(move_index=120.0, vix_index=15.0)
    assert pytest.approx(move_vix, 1e-4) == 8.0


def test_energy_and_refining_spreads():
    # 3:2:1 Crack Spread: Gas $2.50/gal, Heating Oil $2.80/gal, Crude $70/bbl
    # Gas: 2 * (42 * 2.50) = 2 * 105 = 210
    # HO: 1 * (42 * 2.80) = 117.6
    # Total revenue = 327.6; Crude cost = 3 * 70 = 210
    # Crack = (327.6 - 210) / 3 = 117.6 / 3 = 39.20 $/bbl
    crack = calculate_three_two_one_crack_spread(
        gasoline_price_gal=2.50,
        heating_oil_price_gal=2.80,
        crude_oil_price_bbl=70.0,
    )
    assert pytest.approx(crack, 1e-2) == 39.20

    # Brent $85, WTI $80 -> Spread = $5.00
    b_wti = calculate_brent_wti_transatlantic_spread(85.0, 80.0)
    assert b_wti == 5.0

    # Brent $85, Dubai $82 -> EFS = $3.00
    b_dubai = calculate_brent_dubai_efs_spread(85.0, 82.0)
    assert b_dubai == 3.0

    # BTU Energy Parity: Crude $90, NatGas $3.00 -> 90 / (6 * 3) = 90 / 18 = 5.0
    btu = calculate_btu_energy_parity_ratio(90.0, 3.0)
    assert pytest.approx(btu, 1e-4) == 5.0

    # Spark Spread: Power $80/MWh, NatGas $3.00/MMBtu, Carbon $10 -> 80 - 21 - 10 = $49/MWh
    spark = calculate_clean_spark_spread(80.0, 3.0, carbon_cost_per_ton=10.0, heat_rate_mmbtu_per_mwh=7.0)
    assert pytest.approx(spark, 1e-2) == 49.0

    # Dark Spread: Power $80/MWh, Coal $100/ton, Carbon $10 -> 80 - 38 - 8.5 = $33.5/MWh
    dark = calculate_clean_dark_spread(80.0, 100.0, carbon_cost_per_ton=10.0)
    assert pytest.approx(dark, 1e-2) == 33.50


def test_agricultural_and_conversion_spreads():
    # Ammonia: NatGas $3.00 -> 33.5 * 3 + 65 = 100.5 + 65 = $165.50/ton
    nh3 = calculate_haber_bosch_ammonia_cash_cost(3.0)
    assert pytest.approx(nh3, 1e-2) == 165.50

    # Corn/Soy ratio: Soy $13.50, Corn $5.00 -> 13.50 / 5.00 = 2.70 (> 2.50 -> Soy favored)
    res_crop = calculate_corn_soybean_price_ratio(13.50, 5.00)
    assert res_crop["corn_soy_ratio"] == 2.70
    assert res_crop["acreage_regime"] == "SOYBEAN_PLANTING_FAVORED"

    # Soybean Crush: Meal $360/ton, Oil $0.50/lb, Soybean $12.00/bu
    # Meal val = (360/2000)*44 = 0.18 * 44 = 7.92
    # Oil val = 0.50 * 11 = 5.50
    # Total = 13.42 - 12.00 = $1.42/bu
    crush = calculate_soybean_crush_spread(360.0, 0.50, 12.00)
    assert pytest.approx(crush, 1e-2) == 1.42


def test_metals_and_financial_plumbing():
    # Gold $2400, Silver $30 -> 80.0
    au_ag = calculate_gold_silver_ratio(2400.0, 30.0)
    assert pytest.approx(au_ag, 1e-2) == 80.0

    # Steel HRC $900, Ore $120, Coal $250 -> 900 - (1.6*120) - (0.77*250) = 900 - 192 - 192.5 = 515.5
    hrc = calculate_steel_mill_hrc_spread(900.0, 120.0, 250.0)
    assert pytest.approx(hrc, 1e-2) == 515.50

    # HY OAS 350 bps, IG OAS 100 bps -> 3.5
    hy_ig = calculate_high_yield_investment_grade_oas_ratio(350.0, 100.0)
    assert pytest.approx(hy_ig, 1e-2) == 3.5

    # US Net Liquidity: WALCL $7000B, TGA $700B, ON_RRP $300B -> $6000B
    net_liq = calculate_us_net_liquidity(7000.0, 700.0, 300.0)
    assert pytest.approx(net_liq, 1e-2) == 6000.0


def test_evaluate_universal_macro_dashboard():
    inputs = {
        "copper_price_lb": 4.50,
        "gold_price_oz": 2300.0,
        "wti_crude_price_bbl": 78.0,
        "brent_price_bbl": 82.0,
        "gasoline_price_gal": 2.45,
        "heating_oil_price_gal": 2.65,
        "natgas_price_mmbtu": 2.60,
        "soybean_price_bu": 12.00,
        "corn_price_bu": 4.50,
        "walcl_billion": 7100.0,
        "tga_billion": 750.0,
        "onrrp_billion": 400.0,
    }
    dash = evaluate_universal_macro_dashboard(inputs)
    assert "growth_and_risk" in dash
    assert "energy_and_refining" in dash
    assert "agriculture_and_fertilizer" in dash
    assert "metals_and_smelting" in dash
    assert "financial_plumbing_and_liquidity" in dash
    assert "macro_synthesis" in dash
