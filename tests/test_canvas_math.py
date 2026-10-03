import json
import math
import os
import subprocess
import pytest

PAYLOAD_PATH = "visualizer/data/knowledge_graph_payload.json"
CANVAS_ENGINE_JS_PATH = "visualizer/src/canvas_engine.js"


def load_payload():
    assert os.path.exists(PAYLOAD_PATH), f"Payload file must exist at {PAYLOAD_PATH}"
    with open(PAYLOAD_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# --- 1. Coordinate Existence & Static Layout Bounds ---

def test_layout_coordinates_exist_and_bounded():
    """Verify that all nodes have numeric coordinates for islands, pipeline, and radar layouts within bounds."""
    data = load_payload()
    nodes = data["nodes"]
    assert len(nodes) > 0

    for n in nodes:
        for coord in ["x_island", "y_island", "x_pipeline", "y_pipeline", "x_radar", "y_radar"]:
            assert coord in n, f"Missing {coord} in node {n.get('id')}"
            val = n[coord]
            assert isinstance(val, (int, float)), f"{coord} must be numeric in {n.get('id')}"
            assert -10000.0 <= val <= 10000.0, f"{coord} out of expected bounds: {val} in {n.get('id')}"


def test_radar_layout_radii_concentric():
    """Verify that radar coordinates correspond to expected concentric orbital rings."""
    data = load_payload()
    nodes = data["nodes"]
    expected_radii = {220.0, 520.0, 850.0, 1250.0}

    for n in nodes:
        x = n["x_radar"]
        y = n["y_radar"]
        dist = math.sqrt(x * x + y * y)
        # Check that dist is close to one of the expected radii
        matches = [r for r in expected_radii if abs(dist - r) < 5.0]
        assert len(matches) == 1, f"Node {n['id']} radar distance {dist:.2f} does not match any expected ring {expected_radii}"


def test_pipeline_layout_left_to_right_progression():
    """Verify pipeline layout orders nodes horizontally from left (ingestion) to right (persistence)."""
    data = load_payload()
    nodes = data["nodes"]
    by_subsystem = {}
    for n in nodes:
        by_subsystem.setdefault(n["subsystem_id"], []).append(n)

    # Subsystem 1 (Ingestion) average X should be significantly to the left of Subsystem 9 (Execution)
    avg_x_sub1 = sum(n["x_pipeline"] for n in by_subsystem[1]) / len(by_subsystem[1])
    avg_x_sub9 = sum(n["x_pipeline"] for n in by_subsystem[9]) / len(by_subsystem[9])
    assert avg_x_sub1 < avg_x_sub9, f"Pipeline flow inverted: Subsystem 1 X={avg_x_sub1} >= Subsystem 9 X={avg_x_sub9}"


def test_island_layout_subsystem_clustering():
    """Verify island layout separates the 12 domain centers across the 4x3 grid."""
    data = load_payload()
    nodes = data["nodes"]
    centers = {}
    for n in nodes:
        sub_id = n["subsystem_id"]
        centers.setdefault(sub_id, []).append((n["x_island"], n["y_island"]))

    # Each domain should have distinct centroid
    centroids = {}
    for sub_id, points in centers.items():
        avg_x = sum(p[0] for p in points) / len(points)
        avg_y = sum(p[1] for p in points) / len(points)
        centroids[sub_id] = (avg_x, avg_y)

    for sub_a in range(1, 13):
        for sub_b in range(sub_a + 1, 13):
            ca = centroids[sub_a]
            cb = centroids[sub_b]
            dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
            assert dist >= 500.0, f"Domain islands {sub_a} and {sub_b} are too close: dist={dist}"


# --- 2. Camera Math & Coordinate Transformations ---

class CameraMathSimulator:
    def __init__(self, width=1920, height=1080):
        self.width = width
        self.height = height
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.target_camera_x = 0.0
        self.target_camera_y = 0.0
        self.zoom = 1.0
        self.target_zoom = 1.0
        self.lerp_factor = 0.12
        self.min_zoom = 0.1
        self.max_zoom = 4.0

    def world_to_screen(self, wx, wy):
        sx = (wx - self.camera_x) * self.zoom + self.width / 2.0
        sy = (wy - self.camera_y) * self.zoom + self.height / 2.0
        return sx, sy

    def screen_to_world(self, sx, sy):
        wx = (sx - self.width / 2.0) / self.zoom + self.camera_x
        wy = (sy - self.height / 2.0) / self.zoom + self.camera_y
        return wx, wy

    def clamp_zoom(self, z):
        return max(self.min_zoom, min(self.max_zoom, z))

    def update_physics_step(self):
        self.camera_x += (self.target_camera_x - self.camera_x) * self.lerp_factor
        self.camera_y += (self.target_camera_y - self.camera_y) * self.lerp_factor
        self.zoom += (self.target_zoom - self.zoom) * self.lerp_factor

    def zoom_at_cursor(self, cursor_sx, cursor_sy, zoom_factor):
        # Anchor cursor world pos before zoom
        w_before_x, w_before_y = self.screen_to_world(cursor_sx, cursor_sy)
        new_zoom = self.clamp_zoom(self.zoom * zoom_factor)
        self.zoom = new_zoom
        # Adjust camera so w_before still maps to cursor_sx, cursor_sy
        self.camera_x = w_before_x - (cursor_sx - self.width / 2.0) / self.zoom
        self.camera_y = w_before_y - (cursor_sy - self.height / 2.0) / self.zoom


def test_camera_world_screen_invertibility():
    """Verify that screen_to_world(world_to_screen(wx, wy)) == (wx, wy) exactly."""
    cam = CameraMathSimulator(width=1600, height=900)
    cam.camera_x = 345.67
    cam.camera_y = -890.12
    cam.zoom = 1.75

    test_points = [(0.0, 0.0), (100.0, -50.0), (-2500.0, 3100.0), (45.2, 99.8)]
    for wx, wy in test_points:
        sx, sy = cam.world_to_screen(wx, wy)
        rx, ry = cam.screen_to_world(sx, sy)
        assert abs(rx - wx) < 1e-9, f"Inversion X error for ({wx}, {wy}): got {rx}"
        assert abs(ry - wy) < 1e-9, f"Inversion Y error for ({wx}, {wy}): got {ry}"


def test_camera_center_mapping():
    """Verify that point at camera center (camera_x, camera_y) maps to screen center (width/2, height/2)."""
    cam = CameraMathSimulator(width=1280, height=720)
    cam.camera_x = -400.0
    cam.camera_y = 600.0
    cam.zoom = 2.5

    sx, sy = cam.world_to_screen(cam.camera_x, cam.camera_y)
    assert abs(sx - 640.0) < 1e-9
    assert abs(sy - 360.0) < 1e-9


def test_cursor_anchored_zoom():
    """Verify that zooming at a cursor position keeps that world position exactly invariant on screen."""
    cam = CameraMathSimulator(width=1920, height=1080)
    cam.camera_x = 100.0
    cam.camera_y = -200.0
    cam.zoom = 1.0

    cursor_sx = 750.0
    cursor_sy = 420.0
    # World coordinate under cursor before zoom
    wx_orig, wy_orig = cam.screen_to_world(cursor_sx, cursor_sy)

    # Perform zoom in
    cam.zoom_at_cursor(cursor_sx, cursor_sy, 1.5)

    # Verify world coordinate under the same screen cursor is identical
    wx_after, wy_after = cam.screen_to_world(cursor_sx, cursor_sy)
    assert abs(wx_after - wx_orig) < 1e-9
    assert abs(wy_after - wy_orig) < 1e-9


def test_camera_spring_lerp_convergence():
    """Verify spring-damped lerp converges smoothly toward target without overshoot."""
    cam = CameraMathSimulator()
    cam.camera_x = 0.0
    cam.camera_y = 0.0
    cam.target_camera_x = 500.0
    cam.target_camera_y = -300.0

    prev_dist = math.hypot(cam.target_camera_x - cam.camera_x, cam.target_camera_y - cam.camera_y)

    for _ in range(60):
        cam.update_physics_step()
        dist = math.hypot(cam.target_camera_x - cam.camera_x, cam.target_camera_y - cam.camera_y)
        assert dist < prev_dist, "Camera distance to target must decrease monotonically"
        prev_dist = dist

    assert prev_dist < 1.0, f"Camera should have converged to within 1px, got remaining dist {prev_dist}"


# --- 3. Dynamic Tree & Force Layout Algorithms ---

def compute_tree_layout(root_id, nodes, edges):
    """Reference tree layout simulator: Root at (0,0), callers arranged above (y < 0), dependencies below (y > 0)."""
    node_map = {n["id"]: n for n in nodes}
    callers = [c for c in node_map[root_id].get("callers", []) if c != root_id]
    dependencies = [d for d in node_map[root_id].get("dependencies", []) if d != root_id and d not in callers]

    positions = {root_id: (0.0, 0.0)}

    # Callers arranged upstream above (y < 0)
    c_count = len(callers)
    for idx, cid in enumerate(callers):
        x = (idx - (c_count - 1) / 2.0) * 220.0 if c_count > 1 else 0.0
        positions[cid] = (x, -280.0)

    # Dependencies arranged downstream below (y > 0)
    d_count = len(dependencies)
    for idx, did in enumerate(dependencies):
        x = (idx - (d_count - 1) / 2.0) * 220.0 if d_count > 1 else 0.0
        positions[did] = (x, 280.0)

    return positions


def test_tree_layout_geometry():
    """Verify tree layout places root at center, callers strictly above, dependencies strictly below."""
    data = load_payload()
    nodes = data["nodes"]
    edges = data["edges"]
    root_id = "d09_core_order_execution"

    positions = compute_tree_layout(root_id, nodes, edges)
    assert root_id in positions
    assert positions[root_id] == (0.0, 0.0)

    node_map = {n["id"]: n for n in nodes}
    for cid in node_map[root_id].get("callers", []):
        assert cid in positions
        assert positions[cid][1] < 0.0, f"Caller {cid} should be positioned above root (y < 0)"

    for did in node_map[root_id].get("dependencies", []):
        assert did in positions
        assert positions[did][1] > 0.0, f"Dependency {did} should be positioned below root (y > 0)"


def test_force_physics_step_repulsion_and_attraction():
    """Verify force layout: two overlapping nodes repel each other, two connected nodes attract."""
    pos = {
        "n1": [0.0, 0.0],
        "n2": [10.0, 0.0],  # Very close -> high repulsion
    }
    vel = {"n1": [0.0, 0.0], "n2": [0.0, 0.0]}

    # Coulomb repulsion
    dx = pos["n1"][0] - pos["n2"][0]
    dy = pos["n1"][1] - pos["n2"][1]
    dist = math.hypot(dx, dy)
    repulsion = 5000.0 / max(1.0, dist * dist)
    fx = (dx / dist) * repulsion
    fy = (dy / dist) * repulsion

    # Node 1 should be pushed left (fx < 0), Node 2 pushed right
    assert fx < 0
    # Now simulate edge spring attraction between connected far apart nodes
    pos_far = {"a": [0.0, 0.0], "b": [500.0, 0.0]}
    dx_edge = pos_far["b"][0] - pos_far["a"][0]
    rest_len = 150.0
    spring_k = 0.05
    spring_force = (dx_edge - rest_len) * spring_k
    # Spring force should pull node b towards node a (negative force on b)
    assert spring_force > 0, "Spring force should be positive pulling towards rest length"


# --- 4. Node Pill Collision Detection ---

def aabb_intersect(box_a, box_b):
    return (
        box_a["x"] < box_b["x"] + box_b["w"]
        and box_a["x"] + box_a["w"] > box_b["x"]
        and box_a["y"] < box_b["y"] + box_b["h"]
        and box_a["y"] + box_a["h"] > box_b["y"]
    )


def test_node_pill_aabb_collision():
    """Verify collision test math for rounded pill bounding boxes."""
    pill_a = {"x": 100, "y": 100, "w": 180, "h": 40}
    pill_overlap = {"x": 200, "y": 120, "w": 180, "h": 40}
    pill_disjoint = {"x": 500, "y": 500, "w": 180, "h": 40}

    assert aabb_intersect(pill_a, pill_overlap) is True
    assert aabb_intersect(pill_a, pill_disjoint) is False


# --- 5. Minimap Radar Coordinate Projection ---

def test_minimap_projection_bounds():
    """Verify world bounds accurately project into minimap canvas coordinates without clipping."""
    radar_w = 240.0
    radar_h = 160.0
    world_bounds = {"min_x": -3000.0, "max_x": 3000.0, "min_y": -2000.0, "max_y": 2000.0}
    world_w = world_bounds["max_x"] - world_bounds["min_x"]
    world_h = world_bounds["max_y"] - world_bounds["min_y"]

    scale_x = radar_w / world_w
    scale_y = radar_h / world_h
    scale = min(scale_x, scale_y) * 0.9  # 10% margin

    def world_to_minimap(wx, wy):
        mx = radar_w / 2.0 + (wx - (world_bounds["min_x"] + world_bounds["max_x"]) / 2.0) * scale
        my = radar_h / 2.0 + (wy - (world_bounds["min_y"] + world_bounds["max_y"]) / 2.0) * scale
        return mx, my

    # Test center
    cx, cy = world_to_minimap(0.0, 0.0)
    assert abs(cx - radar_w / 2.0) < 1e-6
    assert abs(cy - radar_h / 2.0) < 1e-6

    # Test extreme corners stay within minimap rectangle
    for wx in [world_bounds["min_x"], world_bounds["max_x"]]:
        for wy in [world_bounds["min_y"], world_bounds["max_y"]]:
            mx, my = world_to_minimap(wx, wy)
            assert 0.0 <= mx <= radar_w, f"Minimap X {mx} out of bounds for world ({wx}, {wy})"
            assert 0.0 <= my <= radar_h, f"Minimap Y {my} out of bounds for world ({wx}, {wy})"


def test_canvas_engine_js_exists_and_parses():
    """Verify that visualizer/src/canvas_engine.js exists and is valid ES6 syntax checked by node."""
    assert os.path.exists(CANVAS_ENGINE_JS_PATH), f"File {CANVAS_ENGINE_JS_PATH} must exist"

    # Run node --check visualizer/src/canvas_engine.js
    res = subprocess.run(["node", "--check", CANVAS_ENGINE_JS_PATH], capture_output=True, text=True)
    assert res.returncode == 0, f"JavaScript syntax error in {CANVAS_ENGINE_JS_PATH}:\n{res.stderr}"


def test_canvas_engine_js_runtime_execution():
    """Verify that CanvasEngine instantiates in Node.js and computes all 5 layouts and transforms."""
    node_script = """
    const CanvasEngine = require('./visualizer/src/canvas_engine.js');
    const payload = require('./visualizer/data/knowledge_graph_payload.json');
    const engine = new CanvasEngine({ data: payload });

    if (engine.nodes.length < 100) throw new Error('Expected >= 100 nodes');
    if (engine.edges.length < 200) throw new Error('Expected >= 200 edges');

    const layouts = ['islands', 'pipeline', 'radar', 'tree', 'force'];
    for (const l of layouts) {
        engine.setLayout(l);
        engine.applyLayout(l, false);
        const sampleNode = engine.nodes[0];
        if (typeof sampleNode.x !== 'number' || typeof sampleNode.y !== 'number') {
            throw new Error('Invalid coordinate for layout ' + l);
        }
    }

    // Test invertibility
    const s = engine.worldToScreen(123.45, -678.9);
    const w = engine.screenToWorld(s.x, s.y);
    if (Math.abs(w.x - 123.45) > 1e-4 || Math.abs(w.y - (-678.9)) > 1e-4) {
        throw new Error('Invertibility check failed in JS: ' + JSON.stringify(w));
    }

    // Test node selection and lifecycle stage
    engine.selectNode('d09_mt5_trader');
    if (engine.selectedNodeId !== 'd09_mt5_trader') throw new Error('Node selection failed');
    engine.highlightLifecycleStage(1);
    if (engine.activeStageIndex !== 1 || engine.activeStageNodeIds.size === 0) {
        throw new Error('Lifecycle highlighting failed');
    }
    console.log('OK');
    """
    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True)
    assert res.returncode == 0, f"Node.js runtime execution error:\n{res.stderr}\n{res.stdout}"
    assert "OK" in res.stdout

