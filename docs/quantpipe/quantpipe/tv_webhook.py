"""TradingView alert receiver. Run:  uvicorn quantpipe.tv_webhook:app --host 0.0.0.0 --port 8787
(expose with HTTPS, e.g. cloudflared/ngrok - TradingView only calls public URLs on ports 80/443).

Alert message template (Pine strategy alert -> 'Message' box):
{"secret":"YOUR_SECRET","strategy":"tv_breakout","symbol":"{{ticker}}","action":"{{strategy.order.action}}",
 "price":{{close}},"interval":"{{interval}}","sl":0,"tp":0,"risk_pct":0.5,"time":"{{timenow}}"}
action: buy | sell | close | close_long | close_short
Signals are STORED always; auto-execution only if QP_TV_AUTOEXEC=1 and sl is supplied."""
from __future__ import annotations
import hmac, json, os
from fastapi import FastAPI, Request, HTTPException
from . import store

app = FastAPI(title="quantpipe TradingView webhook")
SECRET = os.getenv("TV_WEBHOOK_SECRET", "")


def norm_symbol(s: str) -> str:
    return s.split(":")[-1].upper().replace("/", "").replace(".P", "")


@app.get("/health")
def health(): return {"ok": True, "secret_set": bool(SECRET)}


@app.get("/signals")
def signals(n: int = 20): return store.get_signals(n)


@app.post("/tv")
async def tv(req: Request):
    raw = (await req.body()).decode("utf-8", "ignore")
    try: p = json.loads(raw)
    except Exception: p = {"raw": raw}
    if not SECRET: raise HTTPException(503, "TV_WEBHOOK_SECRET not configured")
    if not hmac.compare_digest(str(p.pop("secret", "")), SECRET): raise HTTPException(401, "bad secret")
    sym = norm_symbol(str(p.get("symbol", ""))); act = str(p.get("action", "")).lower()
    if act not in {"buy", "sell", "close", "close_long", "close_short"}:
        store.log_event("tv_bad_signal", p, "warn"); raise HTTPException(422, f"bad action {act}")
    sid = store.add_signal("tradingview", sym, act, p)
    result = None
    if os.getenv("QP_TV_AUTOEXEC") == "1":
        from . import mt5_exec as ex
        magic = store.magic_for(p.get("strategy", "tv"))
        if act in ("close", "close_long", "close_short"):
            result = ex.close_all(sym, magic)
        else:
            sl = float(p.get("sl") or 0) or None
            result = ex.place(sym, act, sl, float(p.get("tp") or 0) or None, float(p.get("risk_pct") or 0.5),
                              None, os.getenv("QP_BROKER", "ecn_raw"), p.get("strategy", "tv"), magic)
        store.mark_handled(sid)
    return {"stored": sid, "executed": result}
