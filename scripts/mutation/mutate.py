"""Lightweight AST mutation tester (one mutant at a time per worker copy).

usage: python scripts/mutation/mutate.py --file core/x.py --funcs A.f,g|ALL --tests "t1 t2" [--wide "t3 t4"] --out res.jsonl
Each worker owns its own copy of the repo; the target file is always restored
(original bytes) in a finally block and verified by sha256 at the end.
"""
import argparse, ast, copy, hashlib, json, os, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.environ.get("MUTATION_WORKDIR", os.path.join(tempfile.gettempdir(), "llm_trader_mutation"))
PY = sys.executable
COPIES = [os.path.join(BASE, f"copy_{i}") for i in range(16)]
_SKIP = shutil.ignore_patterns(".git", ".venv", "node_modules", "graphify-out", "__pycache__",
                               "evidence", "raw_downloads", "*.db", "*.db-*", ".hypothesis")


def prepare_copies(n):
    """Fresh worker copies of the current working tree, rebuilt every run.

    Reusing copies across runs once left a mutant from an aborted run in a
    copy, which showed up as a bogus "flaky" test. Never again.
    """
    for c in COPIES[:n]:
        shutil.rmtree(c, ignore_errors=True)
        shutil.copytree(REPO, c, ignore=_SKIP)

CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In,
            ast.Is: ast.IsNot, ast.IsNot: ast.Is}
BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
LOGNAMES = {"log", "logger", "_log", "LOG", "logging"}
STEM_FILE = [""]
REASON_HINTS = ("reason", "error", "block", "violation", "breach", "fail", "alert", "trip", "warning", "halt")


def sym(op):
    return {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
            ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not", ast.Add: "+",
            ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.And: "and", ast.Or: "or"}.get(op, op.__name__)


def annotate(tree):
    """Attach parent and enclosing function qualname to each node."""
    def visit(node, qual, parent, in_excluded):
        for child in ast.iter_child_nodes(node):
            child._parent = node
            q = qual
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{qual}.{child.name}" if qual else child.name
            excl = in_excluded
            if isinstance(child, ast.JoinedStr):
                excl = True
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and \
                    isinstance(child.func.value, ast.Name) and child.func.value.id in LOGNAMES:
                excl = True
            child._qual, child._excl = q, excl
            visit(child, q, node, excl)
    tree._qual, tree._excl = "", False
    visit(tree, "", None, False)


def in_target(node, funcs):
    q = getattr(node, "_qual", "")
    if funcs == ["ALL"]:
        return bool(q)
    return any(q == f or q.startswith(f + ".") for f in funcs)


def is_docstring(node):
    p = getattr(node, "_parent", None)
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def candidates(tree, funcs):
    """Yield (walk_index, kind, sub, lineno, description)."""
    out = []
    for idx, node in enumerate(ast.walk(tree)):
        if getattr(node, "_excl", False) or not in_target(node, funcs):
            continue
        ln = getattr(node, "lineno", 0)
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in CMP_SWAP:
                    out.append((idx, "cmp", i, ln, f"{sym(type(op))} -> {sym(CMP_SWAP[type(op)])}"))
        elif isinstance(node, ast.BoolOp):
            out.append((idx, "bool", 0, ln, f"{sym(type(node.op))} -> {'or' if isinstance(node.op, ast.And) else 'and'}"))
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN_SWAP:
            out.append((idx, "bin", 0, ln, f"{sym(type(node.op))} -> {sym(BIN_SWAP[type(node.op)])}"))
        elif isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool):
                out.append((idx, "const", 0, ln, f"{v} -> {not v}"))
            elif isinstance(v, (int, float)) and not isinstance(getattr(node, "_parent", None), ast.Subscript):
                nv = (v + 1) if isinstance(v, int) else (v * 2 if v != 0 else 1.0)
                out.append((idx, "const", 0, ln, f"{v} -> {nv}"))
        elif isinstance(node, ast.If):
            out.append((idx, "negif", 0, ln, "if C -> if not C"))
        elif isinstance(node, ast.IfExp):
            out.append((idx, "negif", 0, ln, "X if C else Y -> X if not C else Y"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            out.append((idx, "dropnot", 0, ln, "not X -> X"))
        elif isinstance(node, ast.Raise) and node.exc is not None:
            out.append((idx, "delraise", 0, ln, "raise ... -> pass"))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and \
                isinstance(node.value.func, ast.Attribute) and node.value.func.attr in ("append", "extend", "add"):
            tgt = ast.unparse(node.value.func.value).lower()
            if any(h in tgt for h in REASON_HINTS):
                out.append((idx, "delappend", 0, ln, f"delete {ast.unparse(node.value)[:60]}"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("min", "max"):
            out.append((idx, "minmax", 0, ln, f"{node.func.id} -> {'max' if node.func.id == 'min' else 'min'}"))
    return out


class Replace(ast.NodeTransformer):
    def __init__(self, target, new):
        self.target, self.new = target, new

    def generic_visit(self, node):
        for field, old in ast.iter_fields(node):
            if isinstance(old, list):
                for i, v in enumerate(old):
                    if v is self.target:
                        old[i] = self.new
                    elif isinstance(v, ast.AST):
                        self.generic_visit(v)
            elif old is self.target:
                setattr(node, field, self.new)
            elif isinstance(old, ast.AST):
                self.generic_visit(old)
        return node


def apply(src, cand):
    idx, kind, sub, ln, desc = cand
    tree = ast.parse(src)
    node = list(ast.walk(tree))[idx]
    if kind == "cmp":
        node.ops[sub] = CMP_SWAP[type(node.ops[sub])]()
    elif kind == "bool":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "bin":
        node.op = BIN_SWAP[type(node.op)]()
    elif kind == "const":
        v = node.value
        node.value = (not v) if isinstance(v, bool) else ((v + 1) if isinstance(v, int) else (v * 2 if v != 0 else 1.0))
    elif kind == "negif":
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
    elif kind == "dropnot":
        Replace(node, node.operand).visit(tree)
    elif kind in ("delraise", "delappend"):
        Replace(node, ast.Pass()).visit(tree)
    elif kind == "minmax":
        node.func.id = "max" if node.func.id == "min" else "min"
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def run_tests(copy_dir, tests, timeout, prefix, nproc="0"):
    env = dict(os.environ, FAST_TEST="1", LLM_TRADER_OPENBB_OFFLINE="1", PYTHONPYCACHEPREFIX=prefix)
    # Drop any cached bytecode so a same-size/same-second mutant cannot reuse stale pyc.
    stem = os.path.splitext(os.path.basename(STEM_FILE[0]))[0] + "."
    for d, _, files in os.walk(prefix):
        for f in files:
            if f.startswith(stem):
                os.remove(os.path.join(d, f))
    try:
        p = subprocess.run([PY, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "-n", nproc, "-p", "no:randomly",
                            *tests], cwd=copy_dir, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout[-1500:]
    except subprocess.TimeoutExpired:
        return "timeout", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--funcs", required=True)
    ap.add_argument("--tests", required=True)
    ap.add_argument("--wide", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--line-sample", default="", help="A-B=N: keep only N sampled mutants from lines A..B")
    ap.add_argument("--sample", default="", help="func=N,func2=N deterministic sampling per function")
    a = ap.parse_args()
    funcs = a.funcs.split(",")
    rel = a.file
    STEM_FILE[0] = rel
    if not a.list:
        prepare_copies(a.workers)
    orig_path = os.path.join(REPO, rel)
    with open(orig_path, "rb") as fh:
        orig_bytes = fh.read()
    sha = hashlib.sha256(orig_bytes).hexdigest()
    src = orig_bytes.decode()
    tree = ast.parse(src)
    annotate(tree)
    cands = candidates(tree, funcs)
    if a.line_sample:
        import random
        rng, n = a.line_sample.split("=")
        lo, hi = map(int, rng.split("-"))
        inside = [c for c in cands if lo <= c[3] <= hi]
        keep = random.Random(0).sample(inside, min(int(n), len(inside)))
        cands = sorted([c for c in cands if not lo <= c[3] <= hi] + keep, key=lambda c: (c[3], c[0]))
    if a.sample:
        import random
        rules = dict(kv.split("=") for kv in a.sample.split(","))
        q_of = {id(n): getattr(n, "_qual", "") for n in ast.walk(tree)}
        nodes = list(ast.walk(tree))
        keep = []
        for fn, n in rules.items():
            group = [c for c in cands if q_of[id(nodes[c[0]])] == fn]
            rest = random.Random(0).sample(group, min(int(n), len(group)))
            keep += rest
        sampled_fns = set(rules)
        cands = sorted(keep + [c for c in cands if q_of[id(nodes[c[0]])] not in sampled_fns], key=lambda c: (c[3], c[0]))
    if a.limit:
        cands = cands[: a.limit]
    print(f"{rel}: {len(cands)} mutants", flush=True)
    if a.list:
        for c in cands:
            print(c)
        return
    rd = lambda v: open(v[1:]).read().split() if v.startswith("@") else v.split()
    tests, wide = rd(a.tests), rd(a.wide)
    for c in COPIES[: a.workers]:
        with open(os.path.join(c, rel), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == sha, f"copy {c} differs before start"

    # Sanity: unparsed-but-unmutated source must pass the focused tests in every copy.
    plain = ast.unparse(ast.parse(src))
    def sanity(ci):
        c = COPIES[ci]
        p = os.path.join(c, rel)
        try:
            with open(p, "w") as fh:
                fh.write(plain)
            return run_tests(c, tests, a.timeout * 2, os.path.join(BASE, f"pyc_{ci}"))
        finally:
            with open(p, "wb") as fh:
                fh.write(orig_bytes)
    with ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(sanity, range(a.workers)))
    if any(r[0] != 0 for r in res):
        print("SANITY FAILED", res, flush=True)
        sys.exit(2)

    queue = list(enumerate(cands))
    import threading
    lock = threading.Lock()
    outfh = open(a.out, "a")

    def worker(ci):
        c = COPIES[ci]
        p = os.path.join(c, rel)
        prefix = os.path.join(BASE, f"pyc_{ci}")
        while True:
            with lock:
                if not queue:
                    return
                n, cand = queue.pop(0)
            try:
                mutated = apply(src, cand)
            except Exception as exc:
                rec = {"status": "invalid", "err": str(exc)}
            else:
                t0 = time.time()
                try:
                    with open(p, "w") as fh:
                        fh.write(mutated)
                    rc, tail = run_tests(c, tests, a.timeout, prefix)
                    tier = "focused"
                    if rc == 0 and wide:
                        rc, tail = run_tests(c, wide, a.timeout * 6, prefix, "2")
                        tier = "wide"
                finally:
                    with open(p, "wb") as fh:
                        fh.write(orig_bytes)
                status = "survived" if rc == 0 else ("timeout" if rc == "timeout" else
                                                     ("error" if rc in (2, 3, 4) else "killed"))
                rec = {"status": status, "tier": tier, "rc": rc, "secs": round(time.time() - t0, 1)}
                if status == "error":
                    rec["tail"] = tail[-400:]
            src_line = src.splitlines()[cand[3] - 1].strip() if cand[3] else ""
            rec.update({"file": rel, "n": n, "kind": cand[1], "line": cand[3], "desc": cand[4], "src": src_line[:160]})
            with lock:
                outfh.write(json.dumps(rec) + "\n")
                outfh.flush()
                print(f"[{n}] {rec['status']:8s} L{cand[3]} {cand[1]} {cand[4]}", flush=True)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(a.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in COPIES[: a.workers]:
        with open(os.path.join(c, rel), "rb") as fh:
            ok = hashlib.sha256(fh.read()).hexdigest() == sha
        print(f"restore-check {c}: {'OK' if ok else 'MISMATCH'}", flush=True)


if __name__ == "__main__":
    main()
