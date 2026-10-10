import json
import os
import pytest

PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"

VALID_CATEGORIES = {"code_module", "quant_theory", "agent_role", "protective_gate"}
VALID_TIERS = {"L0_Gateway", "L1_Orchestration", "L2_Strategy_Core", "L3_Persistence"}
VALID_GUARDIAN_RESPONSES = {"ALERT", "THROTTLE", "SHUTDOWN"}

EXPECTED_QUANT_MODULES = [
    "ai_trading",
    "asset_pricing",
    "beta_hedging",
    "causal_inference",
    "diversification",
    "drawdown_management",
    "interest_rates",
    "llm_finance",
    "options",
    "pca_factors",
    "regime_detection",
    "time_series",
    "transfer_learning",
    "value_growth",
    "volatility",
]

EXPECTED_KEY_CODE_FILES = [
    "mt5_trader.py",
    "core/order_execution.py",
    "intelligence/llm_orchestrator.py",
    "core/risk_manager.py",
    "core/meta_adaptive_engine.py",
    "research/alpha_miner/regime_detector.py",
]


def load_payload():
    assert os.path.exists(PAYLOAD_PATH), f"Payload file must exist at {PAYLOAD_PATH}"
    with open(PAYLOAD_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_payload_file_exists_and_parses():
    """Verify payload file exists and is valid JSON."""
    data = load_payload()
    assert isinstance(data, dict)


def test_payload_top_level_structure():
    """Verify top-level keys, counts, and collections."""
    data = load_payload()
    assert "nodes" in data, "Payload must contain 'nodes'"
    assert "edges" in data, "Payload must contain 'edges'"
    assert "subsystems" in data, "Payload must contain 'subsystems'"
    assert "lifecycle_stages" in data, "Payload must contain 'lifecycle_stages'"

    assert len(data["nodes"]) >= 100, f"Expected >= 100 nodes, got {len(data['nodes'])}"
    assert len(data["edges"]) >= 200, f"Expected >= 200 edges, got {len(data['edges'])}"
    assert len(data["subsystems"]) == 12, f"Expected 12 subsystems, got {len(data['subsystems'])}"
    assert len(data["lifecycle_stages"]) == 7, f"Expected 7 lifecycle stages, got {len(data['lifecycle_stages'])}"


def test_subsystems_metadata():
    """Verify all 12 operational domains are accurately defined with colors and metadata."""
    data = load_payload()
    subsystems = data["subsystems"]
    subsystem_ids = {s["id"] for s in subsystems}
    assert subsystem_ids == set(range(1, 13)), "Subsystem IDs must be exactly 1 through 12"

    for s in subsystems:
        assert "name" in s and len(s["name"]) > 0
        assert "color" in s and s["color"].startswith("#")
        assert "description" in s and len(s["description"]) > 0


def test_nodes_8_attribute_schema():
    """Verify every node adheres to the complete 8-attribute schema and precalculated coordinates."""
    data = load_payload()
    nodes = data["nodes"]
    node_ids = set()

    for node in nodes:
        nid = node.get("id")
        assert nid, f"Node missing 'id': {node}"
        assert nid not in node_ids, f"Duplicate node id '{nid}'"
        node_ids.add(nid)

        # Core identification
        assert "name" in node and len(node["name"]) > 0
        assert node.get("category") in VALID_CATEGORIES, f"Invalid category {node.get('category')} in {nid}"
        assert 1 <= node.get("subsystem_id", 0) <= 12, f"Invalid subsystem_id in {nid}"
        assert node.get("tier_level") in VALID_TIERS, f"Invalid tier_level in {nid}"

        # Attribute 1: Plain-English Intuition (ELI5)
        assert "plain_english_summary" in node and len(node["plain_english_summary"]) > 15

        # Attribute 2: Mathematical Formulation
        math_form = node.get("mathematical_formulation")
        assert isinstance(math_form, dict), f"mathematical_formulation must be dict in {nid}"
        assert "latex_equation" in math_form
        assert "variables" in math_form and isinstance(math_form["variables"], dict)
        assert "algorithmic_proof" in math_form

        # Attribute 3: Code Realization
        code_real = node.get("code_realization")
        assert isinstance(code_real, dict), f"code_realization must be dict in {nid}"
        assert "file_path" in code_real
        assert "signature" in code_real
        assert "code_snippet" in code_real

        # Attribute 4: Data Contract
        contract = node.get("data_contract")
        assert isinstance(contract, dict), f"data_contract must be dict in {nid}"
        assert "inputs" in contract and isinstance(contract["inputs"], list)
        assert "outputs" in contract and isinstance(contract["outputs"], list)

        # Attribute 5: Failure Modes & Circuit Breaker Responses
        fail_modes = node.get("failure_modes")
        assert isinstance(fail_modes, dict), f"failure_modes must be dict in {nid}"
        assert "edge_case" in fail_modes
        assert "defense_mechanism" in fail_modes
        assert fail_modes.get("guardian_response") in VALID_GUARDIAN_RESPONSES

        # Attribute 6: Callers & Dependencies
        assert isinstance(node.get("callers"), list), f"callers must be list in {nid}"
        assert isinstance(node.get("dependencies"), list), f"dependencies must be list in {nid}"

        # Attribute 7: Cross-Links
        cross_links = node.get("cross_links")
        assert isinstance(cross_links, dict), f"cross_links must be dict in {nid}"

        # Attribute 8: Citations
        citations = node.get("citations")
        assert isinstance(citations, dict), f"citations must be dict in {nid}"
        assert "source_title" in citations
        assert "video_id_or_doi" in citations
        assert "category" in citations

        # Precomputed Layout Coordinates (for Task 2 integration)
        for coord in ["x_island", "y_island", "x_pipeline", "y_pipeline", "x_radar", "y_radar"]:
            assert coord in node, f"Missing coordinate '{coord}' in {nid}"
            assert isinstance(node[coord], (int, float)), f"Coordinate '{coord}' must be numeric in {nid}"


def test_edges_referential_integrity():
    """Verify all edge endpoints reference existing node IDs."""
    data = load_payload()
    node_ids = {n["id"] for n in data["nodes"]}

    for edge in data["edges"]:
        assert "source" in edge, f"Edge missing source: {edge}"
        assert "target" in edge, f"Edge missing target: {edge}"
        assert edge["source"] in node_ids, f"Edge source '{edge['source']}' not found in nodes"
        assert edge["target"] in node_ids, f"Edge target '{edge['target']}' not found in nodes"
        assert "type" in edge, f"Edge missing type: {edge}"


def test_quant_and_codebase_coverage():
    """Verify that all 15 quant modules and key core codebase files are represented."""
    data = load_payload()
    node_ids = {n["id"] for n in data["nodes"]}
    file_paths = {n.get("code_realization", {}).get("file_path") for n in data["nodes"]}

    # Check 15 quant modules
    for mod in EXPECTED_QUANT_MODULES:
        found = any(mod in nid.lower() or mod in str(fp).lower() for nid in node_ids for fp in [file_paths])
        assert found, f"Expected quant module '{mod}' to be represented in graph nodes"

    # Check key codebase files
    for kf in EXPECTED_KEY_CODE_FILES:
        found = any(kf in str(fp) or kf in nid for fp in file_paths for nid in node_ids)
        assert found, f"Expected key codebase file '{kf}' to be represented in graph nodes"


def test_7_stage_trade_lifecycle():
    """Verify the 7-stage trade lifecycle sequence and narrative integrity."""
    data = load_payload()
    node_ids = {n["id"] for n in data["nodes"]}
    stages = data["lifecycle_stages"]

    assert len(stages) == 7
    for idx, stage in enumerate(stages, start=1):
        assert stage.get("stage_index") == idx
        assert "name" in stage and len(stage["name"]) > 0
        assert "duration_ms" in stage and stage["duration_ms"] > 0
        assert "narrative" in stage and len(str(stage["narrative"])) > 10
        assert "active_nodes" in stage and isinstance(stage["active_nodes"], list)
        assert len(stage["active_nodes"]) > 0, f"Stage {idx} must have active nodes"
        for nid in stage["active_nodes"]:
            assert nid in node_ids, f"Stage {idx} active node '{nid}' not found in nodes"


@pytest.mark.xfail(strict=True, reason="ROADMAP Phase 10: graph data cites compliance_dashboard.html, which is not in the repo")
def test_file_paths_are_grounded_in_repository():
    """Every node must cite a file that really exists, or be flagged ungrounded."""
    data = load_payload()
    ungrounded = []

    for node in data["nodes"]:
        code_real = node["code_realization"]
        assert "grounded" in code_real, f"Node {node['id']} is missing the grounding flag"
        if code_real["grounded"]:
            assert os.path.exists(code_real["file_path"]), (
                f"Node {node['id']} cites missing file '{code_real['file_path']}'"
            )
        else:
            assert code_real.get("grounding_note"), (
                f"Ungrounded node {node['id']} must explain why"
            )
            ungrounded.append(node["id"])

    assert not ungrounded, f"Nodes without a real implementing file: {ungrounded}"


def test_repointed_paths_record_their_declared_origin():
    """Re-pointed nodes keep the declared module name as provenance."""
    data = load_payload()
    repointed = 0

    for node in data["nodes"]:
        code_real = node["code_realization"]
        declared = code_real.get("declared_path")
        if not declared:
            continue
        repointed += 1
        assert declared != code_real["file_path"], (
            f"Node {node['id']} records a redundant declared_path"
        )
        assert os.path.exists(code_real["file_path"])

    assert repointed >= 40, f"Expected at least 40 re-pointed paths, got {repointed}"


def test_real_loc_and_graphify_symbol_provenance():
    """Line counts are measured from the real file and carry AST symbol counts."""
    data = load_payload()
    attributed = 0

    for node in data["nodes"]:
        assert isinstance(node.get("loc"), int) and node["loc"] > 0, (
            f"Node {node['id']} has no measured LOC"
        )
        code_real = node["code_realization"]
        if code_real.get("graphify_symbols"):
            attributed += 1
            assert code_real["graphify_symbols"] > 0

    assert attributed >= 100, f"Expected >= 100 symbol-attributed nodes, got {attributed}"


def test_graphify_ast_provenance_edges():
    """Edges must include call relationships extracted from graphify-out/graph.json."""
    data = load_payload()
    call_edges = [e for e in data["edges"] if e["type"] == "code_calls"]

    assert len(call_edges) >= 50, f"Expected >= 50 graphify call edges, got {len(call_edges)}"

    for edge in call_edges:
        assert edge.get("provenance") == "graphify-out/graph.json"
        assert edge["weight"] >= 1.0
        assert "AST call site" in edge["label"]
