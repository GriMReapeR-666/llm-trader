"""Headless MT5 build + Strategy Tester via the terminal's documented command-line interface (no DLL bridge needed):
   metaeditor64.exe /compile:<file.mq5> /log:<log>        terminal64.exe /config:<tester.ini>   (ShutdownTerminal=1)
Env: MT5_TERMINAL_EXE, MT5_METAEDITOR_EXE (optional), MT5_DATA_DIR (File > Open Data Folder).
STRONGLY recommended: a dedicated PORTABLE install (terminal64.exe /portable) so a tester launch never collides
with your live terminal. Windows only. NOT executed in the sandbox this was written in - validate on your machine."""
from __future__ import annotations
import os, re, subprocess, sys, time
from pathlib import Path

MODELS = {"every_tick": 0, "ohlc_1m": 1, "open_prices": 2, "real_ticks": 4}


def _cfg():
    exe = os.getenv("MT5_TERMINAL_EXE"); data = os.getenv("MT5_DATA_DIR")
    if not exe or not data: raise RuntimeError("set MT5_TERMINAL_EXE and MT5_DATA_DIR")
    me = os.getenv("MT5_METAEDITOR_EXE") or str(Path(exe).with_name("metaeditor64.exe"))
    return Path(exe), Path(me), Path(data)


def _win():
    if sys.platform != "win32": raise RuntimeError("MT5 compile/tester automation only works on Windows")


def install_source(name: str, source: str) -> Path:
    _, _, data = _cfg(); d = data / "MQL5" / "Experts" / "QP"; d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.mq5"; p.write_text(source, encoding="utf-16"); return p      # MetaEditor expects UTF-16/UTF-8-BOM


def parse_compile_log(text: str) -> dict:
    errs = re.findall(r"^(.*\(\d+,\d+\)\s*:\s*error.*)$", text, re.M | re.I)
    warns = re.findall(r"^(.*\(\d+,\d+\)\s*:\s*warning.*)$", text, re.M | re.I)
    m = re.search(r"Result:\s*(\d+)\s*errors?,\s*(\d+)\s*warnings?", text, re.I)
    ne = int(m.group(1)) if m else len(errs)
    return {"ok": ne == 0 and bool(m or not errs), "errors": errs[:25], "warnings": warns[:10], "summary": m.group(0) if m else None}


def compile_ea(mq5: Path, timeout=120) -> dict:
    """Returns compiler errors with line/col so the LLM can patch the SPEC/exporter and retry."""
    _win(); _, me, _ = _cfg(); log = mq5.with_suffix(".log")
    subprocess.run([str(me), f"/compile:{mq5}", f"/log:{log}"], timeout=timeout, capture_output=True)
    txt = log.read_bytes().decode("utf-16", "ignore") if log.exists() else ""
    if "error" not in txt.lower() and "result" not in txt.lower() and log.exists():
        txt = log.read_text(errors="ignore")
    r = parse_compile_log(txt); r["ex5_exists"] = mq5.with_suffix(".ex5").exists(); r["ok"] = r["ok"] and r["ex5_exists"]
    return r


def build_ini(expert: str, symbol: str, tf: str, date_from: str, date_to: str, deposit=10000, leverage=100,
              model="ohlc_1m", report="qp_report", inputs: dict | None = None, currency="USD", delay_ms=50) -> str:
    """date format YYYY.MM.DD. expert is relative to MQL5\\Experts, e.g. QP\\macro_surprise."""
    lines = ["[Tester]", f"Expert={expert}", f"Symbol={symbol}", f"Period={tf}", "Optimization=0", f"Model={MODELS[model]}",
             f"FromDate={date_from}", f"ToDate={date_to}", "ForwardMode=0", f"Deposit={deposit}", f"Currency={currency}",
             f"Leverage={leverage}", f"ExecutionMode={delay_ms}", "Visual=0", f"Report={report}", "ReplaceReport=1", "ShutdownTerminal=1"]
    if inputs:
        lines.append("[TesterInputs]"); lines += [f"{k}={v}" for k, v in inputs.items()]
    return "\r\n".join(lines) + "\r\n"


def _num(s: str) -> float | None:
    s = re.sub(r"[^\d.\-]", "", s.replace("\xa0", "").replace(" ", "").replace(",", ""))
    try: return float(s)
    except ValueError: return None


LABELS = {"Total Net Profit": "net_profit", "Profit Factor": "profit_factor", "Expected Payoff": "expected_payoff",
          "Recovery Factor": "recovery_factor", "Sharpe Ratio": "sharpe", "Total Trades": "trades",
          "Balance Drawdown Maximal": "balance_dd_max", "Equity Drawdown Maximal": "equity_dd_max",
          "Profit Trades (% of total)": "win_rate_pct", "Gross Profit": "gross_profit", "Gross Loss": "gross_loss",
          "Initial Deposit": "initial_deposit", "History Quality": "history_quality"}


def parse_report(html: str) -> dict:
    text = re.sub(r"<[^>]+>", "|", html); text = re.sub(r"\|\s*(\|\s*)+", "|", text)
    out = {}
    for label, key in LABELS.items():
        m = re.search(re.escape(label) + r":?\s*\|\s*([^|]+)", text)
        if m: out[key] = _num(m.group(1))
    return out


def run_tester(expert: str, symbol: str, tf: str, date_from: str, date_to: str, timeout=3600, **kw) -> dict:
    _win(); exe, _, data = _cfg(); rep = f"qp_{int(time.time())}"
    ini = data / f"{rep}.ini"; ini.write_text(build_ini(expert, symbol, tf, date_from, date_to, report=rep, **kw), encoding="utf-8")
    subprocess.run([str(exe), f"/config:{ini}"], timeout=timeout)
    for base in (data, exe.parent):
        for ext in (".htm", ".html"):
            p = base / f"{rep}{ext}"
            if p.exists():
                raw = p.read_bytes(); html = raw.decode("utf-16", "ignore") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "ignore")
                return {"ok": True, "report_file": str(p), "metrics": parse_report(html)}
    return {"ok": False, "error": "no report found - check Journal/Tester logs; was the terminal already running (use /portable)?"}
