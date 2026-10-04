import os, sys, json, shutil, subprocess, time, tempfile
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
QKF = os.getcwd()
SRC = os.path.join(QKF, "src")
sys.path.insert(0, SRC)

KB_DIR = os.path.join(QKF, "data", "knowledge_base")
IDX_PATH = os.path.join(KB_DIR, "index.json")
SEARCH_IDX_PATH = os.path.join(KB_DIR, "search_index.json")

results = {"pass": [], "warn": [], "fail": []}


def check(name, ok, detail=""):
    results["pass" if ok else "fail"].append(f"{name}" + (f" — {detail}" if detail else ""))
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def run_cli(args, timeout=300):
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "quant_framework.cli"] + args,
        cwd=QKF, capture_output=True, text=True, env=env, timeout=timeout,
    )
    return proc, time.perf_counter() - t0


# ============================================================================
print("=" * 70)
print("SECTION 1: CLI subprocesses (real user entry points)")
print("=" * 70)

proc, dt = run_cli(["stats"])
check("CLI stats exits 0", proc.returncode == 0, f"{dt:.2f}s")
check("CLI stats prints per-topic counts", "Knowledge Base Statistics" in proc.stdout
      and "01_Regime_Detection" in proc.stdout)
stats_counts = {}
for line in proc.stdout.splitlines():
    line = line.strip()
    if ":" in line and line.split(":")[0].strip().startswith(("0", "9")):
        head, tail = line.split(":", 1)
        tail_digits = tail.strip().split()
        if tail_digits and tail_digits[0].isdigit():
            stats_counts[head.strip()] = int(tail_digits[0])
print("  parsed stats:", stats_counts)

proc, dt = run_cli(["search", "kelly criterion"])
check("CLI search exits 0", proc.returncode == 0, f"{dt:.2f}s")
cli_hits = sum(1 for line in proc.stdout.splitlines() if line.strip().startswith("- "))
check("CLI search returns hits", cli_hits > 0, f"{cli_hits} hits")

proc, dt = run_cli(["search", "zzznotarealterm"])
check("CLI search empty query exits 0", proc.returncode == 0)
check("CLI search empty query returns 0", "Found 0 results" in proc.stdout)

proc, dt = run_cli(["--help"])
check("CLI --help lists all 8 commands",
      proc.returncode == 0 and all(c in proc.stdout for c in
      ["download", "transcribe", "classify", "search", "stats", "reclassify", "build", "agent"]))

shell_input = (
    "tools\n"
    "exec quant_utilities.expectancy.calculate {\"win_rate\": 0.55, \"avg_win_r\": 2.0, \"avg_loss_r\": 1.0}\n"
    "chain [{\"tool\": \"portfolio_management.kelly.calculate\", \"params\": {\"win_rate\": 0.6, \"win_loss_ratio\": 1.5}, \"save_as\": \"k\"}]\n"
    "exit\n"
)
t0 = time.perf_counter()
shell = subprocess.run(
    [sys.executable, "-X", "utf8", "-m", "quant_framework.cli", "agent"],
    cwd=QKF, input=shell_input, capture_output=True, text=True, env=env, timeout=120,
)
dt = time.perf_counter() - t0
check("CLI agent shell session exits 0", shell.returncode == 0, f"{dt:.2f}s")
check("agent shell lists 19 tools", "Found 19 tools" in shell.stdout)
check("agent shell exec returns EV 0.65", "success" in shell.stdout and "0.65" in shell.stdout)
check("agent shell chain succeeds", "success': True" in shell.stdout)

# ============================================================================
print("=" * 70)
print("SECTION 2: Agent battery over the real KB (in-process)")
print("=" * 70)

from quant_framework.config import Config
from quant_framework.agent import QuantAgent
import numpy as np

agent = QuantAgent("config/sources.yaml")
tools = agent.discover_tools()
check("agent discovers 19 tools", len(tools) == 19, str(len(tools)))

r = agent.execute_tool("knowledge.search", query="volatility")
check("knowledge.search volatility", r["success"] and len(r["result"]) >= 150,
      f"{len(r['result'])} hits")
r = agent.execute_tool("knowledge.search", query="kelly criterion")
check("knowledge.search AND query", r["success"] and len(r["result"]) >= 1,
      f"{len(r['result'])} hits")

r = agent.execute_tool("knowledge.classify",
                       text="hidden markov model for regime detection with volatility")
check("knowledge.classify topic", r["success"] and r["result"]["topic"] == "01_Regime_Detection",
      r["result"].get("topic", ""))

topics = agent.execute_tool("knowledge.list_topics")["result"]["topics"]
check("knowledge.list_topics has 6 topics", len(topics) == 6, str(sorted(topics)))

summary_path = os.path.join(KB_DIR, "01_Regime_Detection", "regime_detection_summary.txt")
sample = agent.execute_tool("knowledge.get_content", path=summary_path)
check("knowledge.get_content reads real file",
      sample["success"] and len(sample["result"]) > 100, f"{len(sample['result'])} chars")

steps = np.zeros(1200)
eps = np.random.RandomState(7).randn(1200) * 0.01
for t in range(1, 1200):
    steps[t] = 0.8 * steps[t - 1] + eps[t]
m = agent.execute_tool("regime_detection.hurst.calculate", prices=(100 + np.cumsum(steps)).tolist())
check("hurst trending >0.55 via agent", m["success"] and m["result"]["hurst_exponent"] > 0.55,
      f"H={m['result']['hurst_exponent']:.3f}")

m = agent.execute_tool("regime_detection.hurst.calculate",
                       prices=(100 + np.cumsum(np.random.RandomState(3).randn(1200))).tolist())
check("hurst random walk ~0.5 via agent", m["success"] and 0.4 < m["result"]["hurst_exponent"] < 0.6,
      f"H={m['result']['hurst_exponent']:.3f}")

two_block = np.concatenate([np.random.RandomState(42).randn(150) * 0.005,
                            np.random.RandomState(43).randn(150) * 0.05]).tolist()
m = agent.execute_tool("regime_detection.hmm.classify", returns=two_block)
check("hmm classify auto-fit labels",
      m["success"] and m["result"]["regime_label"] in ("calm", "normal", "high_vol"),
      f"label={m['result'].get('regime_label')} conf={m['result'].get('confidence', 0):.2f}")

m = agent.execute_tool("portfolio_management.kelly.calculate",
                       win_rate=0.6, win_loss_ratio=1.5, fraction=0.25)
check("kelly golden via agent", m["success"]
      and abs(m["result"]["kelly_fraction"] - 1 / 3) < 1e-9
      and abs(m["result"]["prob_ever_half"] - 0.5 ** 7) < 1e-12,
      f"kelly={m['result']['kelly_fraction']:.4f} p_half={m['result']['prob_ever_half']:.5f}")

iid = [(np.random.RandomState(s).randn(500) * 0.01).tolist() for s in (1, 2, 3)]
m = agent.execute_tool("portfolio_management.hrp.optimize", returns=iid)
w = m["result"]["weights"]
check("hrp 3 iid near-equal via agent", m["success"] and all(abs(x - 1 / 3) < 0.03 for x in w),
      str([round(x, 3) for x in w]))

m = agent.execute_tool("quant_utilities.expectancy.r_multiple", entry=100.0, exit=110.0, stop=95.0)
check("r_multiple dollar_profit=10", m["success"]
      and abs(m["result"]["dollar_profit"] - 10.0) < 1e-12
      and abs(m["result"]["r_multiple"] - 2.0) < 1e-12)

m = agent.execute_tool("quant_utilities.drawdown.analyze",
                       equity_curve=[100, 110, 90, 95, 120, 80, 130])
check("drawdown golden via agent", m["success"] and abs(m["result"]["max_drawdown"] - 40 / 120) < 1e-12)

m = agent.execute_tool("order_execution.almgren_chriss.calculate",
                       order_size=10000, adv=1_000_000, price=100.0, volatility=0.02)
check("almgren-chriss positive impact", m["success"] and m["result"]["total_impact_bps"] > 0)

m = agent.execute_tool("strategy_development.dsr.calculate", sharpe_ratio=0.10, n=252, trials=1000)
check("dsr rejects small edge among 1000 trials", m["success"] and m["result"]["dsr"] < 0.5,
      f"dsr={m['result']['dsr']:.3f} sr0={m['result']['expected_max_sr']:.3f}")

m = agent.execute_tool("strategy_development.triple_barrier.label",
                       prices=[100, 101, 102, 103, 104, 105, 106], entry_price=100.0)
check("triple barrier TP exit", m["success"] and m["result"]["label"] == 1 and m["result"]["exit_bar"] == 5)

m = agent.execute_tool("strategy_development.wfo.optimize",
                       returns=(np.random.RandomState(0).randn(600) * 0.01).tolist())
check("wfo flags noise as not robust", m["success"] and m["result"]["robust"] is False)

# ============================================================================
print("=" * 70)
print("SECTION 3: Data integrity + pipeline artifacts")
print("=" * 70)

with open(IDX_PATH, encoding="utf-8") as f:
    idx = json.load(f)
check("index.json has 6 topics", len(idx) == 6, str(sorted(idx.keys())))

missing = [fp for files in idx.values() for fp in files if not os.path.exists(fp)]
check("every index.json path exists on disk", not missing, f"{len(missing)} missing")

disk_counts = {}
for topic in sorted(os.listdir(KB_DIR)):
    d = os.path.join(KB_DIR, topic)
    if os.path.isdir(d):
        disk_counts[topic] = len([f for f in os.listdir(d) if f.endswith((".txt", ".md"))])
check("index.json counts match disk", disk_counts == {k: len(v) for k, v in idx.items()},
      str(disk_counts))

with open(SEARCH_IDX_PATH, encoding="utf-8") as f:
    sidx = json.load(f)
# The index stores paths in the config's relative form (same convention as
# index.json); compare resolved locations, not raw strings.
disk_docs = {str(p.resolve()) for p in Path(KB_DIR).rglob("*") if p.is_file() and p.suffix in (".txt", ".md")}
idx_docs = {str(Path(p).resolve()) for p in sidx["doc_list"]}
check("search_index.json doc_list matches disk set",
      idx_docs == disk_docs, f"{len(sidx['doc_list'])} docs indexed")
check("stats CLI total matches index.json total",
      sum(stats_counts.values()) == sum(len(v) for v in idx.values()),
      f"stats={sum(stats_counts.values())} index={sum(len(v) for v in idx.values())}")

trans_dir = Path(QKF, "data", "transcriptions")
trans_empty = not any(trans_dir.rglob("*.txt")) if trans_dir.is_dir() else True
check("transcriptions dir drained (idempotence state)", trans_empty)

# classify-stage idempotence in an isolated sandbox (never touches real data)
sandbox = tempfile.mkdtemp(prefix="qkf_e2e_")
try:
    pkg = os.path.join(sandbox, "pkg")
    os.makedirs(os.path.join(pkg, "config"))
    os.makedirs(os.path.join(pkg, "data", "transcriptions"))
    with open(os.path.join(pkg, "config", "sources.yaml"), "w", encoding="utf-8") as f:
        f.write("paths:\n  transcriptions: data/transcriptions\n  knowledge_base: data/knowledge_base\n")
    with open(os.path.join(pkg, "data", "transcriptions", "t.txt"), "w", encoding="utf-8") as f:
        f.write("kelly criterion quarter kelly risk of ruin")
    prev = os.getcwd()
    os.chdir(pkg)
    try:
        from quant_framework.pipeline.orchestrator import KnowledgePipeline
        pipe = KnowledgePipeline("config/sources.yaml")
        pipe._run_classify_stage()
        r2 = pipe._run_classify_stage()
    finally:
        os.chdir(prev)
    check("classify stage rerun is flagged no-op", r2.metadata.get("idempotent_noop") is True)
    check("classify rerun keeps index fresh",
          os.path.exists(os.path.join(pkg, "data", "knowledge_base", "index.json")))
finally:
    shutil.rmtree(sandbox, ignore_errors=True)

# reclassify CLI semantics in a sandbox copy of the real KB
sandbox2 = tempfile.mkdtemp(prefix="qkf_e2e2_")
try:
    os.makedirs(os.path.join(sandbox2, "config"))
    shutil.copytree(KB_DIR, os.path.join(sandbox2, "data", "knowledge_base"))
    shutil.copy(os.path.join(QKF, "config", "sources.yaml"), os.path.join(sandbox2, "config", "sources.yaml"))
    prev = os.getcwd()
    os.chdir(sandbox2)
    try:
        for mod in list(sys.modules):
            if mod.startswith("quant_framework"):
                del sys.modules[mod]
        from quant_framework.cli import main as cli_main
        sys.argv = ["quant-knowledge", "reclassify"]
        rc = cli_main()
    finally:
        os.chdir(prev)
        sys.argv = ["quant-knowledge"]
    check("CLI reclassify (sandbox copy) exits 0", rc == 0, f"rc={rc}")
finally:
    shutil.rmtree(sandbox2, ignore_errors=True)

# ============================================================================
print("=" * 70)
print("SECTION 4: Test suite")
print("=" * 70)

pytest_proc = subprocess.run(
    [sys.executable, "-X", "utf8", "-m", "pytest", "tests", "-p", "no:warnings", "-n0", "-q", "--tb=short"],
    cwd=QKF, capture_output=True, text=True, env=env, timeout=300,
)
suite_line = pytest_proc.stdout.strip().splitlines()[-1] if pytest_proc.stdout.strip() else ""
check("full test suite green", pytest_proc.returncode == 0, suite_line)

# ============================================================================
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"PASS: {len(results['pass'])}  WARN: {len(results['warn'])}  FAIL: {len(results['fail'])}")
for f_ in results["fail"]:
    print("FAILED:", f_)
for w_ in results["warn"]:
    print("WARNED:", w_)
print("E2E-VERDICT:", "GREEN" if not results["fail"] else "RED")
print("E2E-DONE")
