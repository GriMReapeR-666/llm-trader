import pytest

from macro_source_policy import assess_source
from macro_graph import MacroGraph
from historical_regimes import HISTORICAL_REGIMES, get_template, template_registry


def test_macro_source_policy_tiering():
    # Primary domain assessment
    res_fed = assess_source(url="https://www.federalreserve.gov/releases/h15/")
    assert res_fed["allowed"] is True
    assert res_fed["tier"] == "PRIMARY"

    res_fred = assess_source(url="https://fred.stlouisfed.org/series/CPIAUCSL")
    assert res_fred["allowed"] is True
    assert res_fred["tier"] == "PRIMARY"

    # Runtime feed assessment
    res_feed = assess_source(url="https://broker.local/calendar", publisher="official calendar provider")
    assert res_feed["allowed"] is True
    assert res_feed["tier"] == "RUNTIME_FEED"

    # Unverified domain assessment
    res_unverified = assess_source(url="https://random-unverified-blog.org/news")
    assert res_unverified["allowed"] is False
    assert res_unverified["tier"] == "UNVERIFIED"


def test_macro_graph_edges_and_rejection(tmp_path):
    db_file = str(tmp_path / "macro.db")
    graph = MacroGraph(db_file)

    # Ingest a valid primary source payload
    source_id = graph.ingest_source(
        title="Fed Rate Decision Statement",
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20220615a.htm",
        publisher="Federal Reserve Board",
        payload={"rate_hike_bps": 75}
    )
    assert source_id.startswith("macro:")

    # Create edge from valid primary source
    edge_id = graph.propose_edge(
        source_name="CPI_SURPRISE",
        source_type="MACRO_INDICATOR",
        target_name="FED_FUNDS_RATE",
        target_type="POLICY_RATE",
        relation="DRIVES_POLICY",
        sign="POSITIVE",
        confidence=0.90,
        source_id=source_id,
        notes="High inflation drives hawkish rate hikes"
    )
    assert edge_id > 0

    # Ingest unverified source and verify edge rejection
    unverified_source_id = graph.ingest_source(
        title="Unverified Rumor",
        source_url="https://unverified-blog.com/post/123",
        publisher="Anonymous Blog",
        payload={"rumor": "rate cut tomorrow"}
    )
    with pytest.raises(ValueError, match="not approved by source policy"):
        graph.propose_edge(
            source_name="CPI_SURPRISE",
            source_type="MACRO_INDICATOR",
            target_name="FED_FUNDS_RATE",
            target_type="POLICY_RATE",
            relation="RUMOR_DRIVES_POLICY",
            sign="NEGATIVE",
            confidence=0.50,
            source_id=unverified_source_id,
            notes="Unverified blog post"
        )


def test_historical_regimes_catalog():
    # Verify key historical regimes are present in catalog
    assert "great_inflation_1965_1982" in HISTORICAL_REGIMES
    assert "great_recession_2007_2009" in HISTORICAL_REGIMES
    assert "pandemic_2020" in HISTORICAL_REGIMES
    assert "inflation_rates_2022" in HISTORICAL_REGIMES
    assert "banking_stress_2023" in HISTORICAL_REGIMES

    # Verify template details for 2022 inflation regime
    regime_2022 = get_template("inflation_rates_2022")
    assert regime_2022 is not None
    assert regime_2022["label"] == "2022 inflation and tightening cycle"
    assert "federalreserve.gov" in regime_2022["source_url"]
    assert "inflation_surprise" in regime_2022["required_fields"]

    regimes_list = template_registry()
    assert len(regimes_list) >= 5
