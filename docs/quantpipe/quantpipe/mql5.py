"""Spec -> MQL5 Expert Advisor. Supports: sma ema rsi atr highest lowest macd macd_signal macd_hist adx
+ all ev_* event fields (read from a CSV in MQL5\\Files, because the MQL5 calendar API does NOT work in the
Strategy Tester). Unsupported indicator types raise ValueError (use the Python engine for those).
Parity note: MT5's iATR/iRSI/iADX seeding differs slightly from the pandas versions, so trade counts and P&L
will be close but not identical - treat MT5 tester as the execution-realism check, Python as the research engine."""
from __future__ import annotations
import zlib
from .strategy import validate, EVENT_FIELDS, SERIES
from .events import EVENT_FIELDS as _EVF

EXPORTABLE = {"sma", "ema", "rsi", "atr", "highest", "lowest", "macd", "macd_signal", "macd_hist", "adx"}
APPLIED = {"close": "PRICE_CLOSE", "open": "PRICE_OPEN", "high": "PRICE_HIGH", "low": "PRICE_LOW"}
TFC = {"M1": "PERIOD_M1", "M5": "PERIOD_M5", "M15": "PERIOD_M15", "M30": "PERIOD_M30", "H1": "PERIOD_H1", "H4": "PERIOD_H4", "D1": "PERIOD_D1"}
OPMAP = {">": ">", "<": "<", ">=": ">=", "<=": "<="}


def _hname(n): return f"h_{n}"


def export_mq5(spec: dict, magic: int | None = None) -> str:
    errs = validate(spec)
    if errs: raise ValueError(f"invalid spec: {errs}")
    ind = spec["indicators"]
    bad = [f"{k}:{v['type']}" for k, v in ind.items() if v["type"] not in EXPORTABLE]
    if bad: raise ValueError(f"not exportable to MQL5: {bad}. Exportable: {sorted(EXPORTABLE)}")
    name = spec["name"]; sym = spec["symbol"].upper(); r = spec["risk"]; f = spec.get("filters", {}); evc = spec.get("events", {})
    magic = magic if magic is not None else zlib.crc32(name.encode()) % 10**6
    use_ev = any(x in str([spec.get(k) for k in ("entry_long", "entry_short", "exit_long", "exit_short")]) for x in EVENT_FIELDS) \
        or "event_blackout" in f or bool(r.get("flatten_before_event_min"))
    inputs, handles, inits = [], [], []
    for k, v in ind.items():
        t = v["type"]; p = int(v.get("period", 14)); ap = APPLIED[v.get("source", "close")]
        if t in ("macd", "macd_signal", "macd_hist"):
            fa, sl, sg = int(v.get("fast", 12)), int(v.get("slow", 26)), int(v.get("signal", 9))
            inputs += [f"input int P_{k}_fast = {fa};", f"input int P_{k}_slow = {sl};", f"input int P_{k}_sig = {sg};"]
            inits.append(f"   {_hname(k)} = iMACD(_Symbol, TF, P_{k}_fast, P_{k}_slow, P_{k}_sig, {ap});")
        elif t in ("sma", "ema"):
            inputs.append(f"input int P_{k} = {p};"); inits.append(f"   {_hname(k)} = iMA(_Symbol, TF, P_{k}, 0, {'MODE_EMA' if t == 'ema' else 'MODE_SMA'}, {ap});")
        elif t == "rsi":
            inputs.append(f"input int P_{k} = {p};"); inits.append(f"   {_hname(k)} = iRSI(_Symbol, TF, P_{k}, {ap});")
        elif t == "atr":
            inputs.append(f"input int P_{k} = {p};"); inits.append(f"   {_hname(k)} = iATR(_Symbol, TF, P_{k});")
        elif t == "adx":
            inputs.append(f"input int P_{k} = {p};"); inits.append(f"   {_hname(k)} = iADX(_Symbol, TF, P_{k});")
        else:  # highest / lowest
            inputs.append(f"input int P_{k} = {p};")
        if t not in ("highest", "lowest"):
            handles.append(f"int {_hname(k)} = INVALID_HANDLE;")
            inits.append(f"   if({_hname(k)} == INVALID_HANDLE) {{ Print(\"indicator {k} failed\"); return INIT_FAILED; }}")

    def op(o, s: str) -> str:
        if isinstance(o, (int, float)): return repr(float(o))
        if o in SERIES: return f"i{o.capitalize()}(_Symbol, TF, {s})"
        if o in EVENT_FIELDS: return {"ev_mins_since": f"EvSince(EvBc({s}))", "ev_mins_to_next": f"EvNext(EvBc({s}))",
                                      "ev_surprise_net": f"EvSurprise(EvBc({s}))", "ev_in_bar": f"EvInBar(EvBc({s}))"}[o]
        v = ind[o]; t = v["type"]
        if t in ("sma", "ema", "rsi", "atr", "adx"): return f"Buf({_hname(o)}, 0, {s})"
        if t == "macd": return f"Buf({_hname(o)}, 0, {s})"
        if t == "macd_signal": return f"Buf({_hname(o)}, 1, {s})"
        if t == "macd_hist": return f"(Buf({_hname(o)}, 0, {s}) - Buf({_hname(o)}, 1, {s}))"
        fn = "HH" if t == "highest" else "LL"
        return f"{fn}(P_{o}, {int(v.get('shift', 1))}, {s})"

    def cond(c) -> str:
        a, o, b = c
        if o in OPMAP: return f"({op(a, '1')} {OPMAP[o]} {op(b, '1')})"
        if o == "crosses_above": return f"({op(a, '1')} > {op(b, '1')} && {op(a, '2')} <= {op(b, '2')})"
        return f"({op(a, '1')} < {op(b, '1')} && {op(a, '2')} >= {op(b, '2')})"

    def allc(cs): return " && ".join(cond(c) for c in cs) if cs else "false"

    flt = ["bool Filters() {", "   datetime ts = iTime(_Symbol, TF, 1); datetime u = ServerToUtc(ts); MqlDateTime d; TimeToStruct(u, d);"]
    if "hours_utc" in f:
        h0, h1 = f["hours_utc"]
        flt.append(f"   if(!({'(d.hour >= %d && d.hour < %d)' % (h0, h1) if h0 <= h1 else '(d.hour >= %d || d.hour < %d)' % (h0, h1)})) return false;")
    if "weekdays" in f:
        days = " || ".join(f"((d.day_of_week + 6) % 7 == {int(x)})" for x in f["weekdays"])
        flt.append(f"   if(!({days})) return false;")
    if "min_adx" in f:
        a = next((k for k, v in ind.items() if v["type"] == "adx"), None)
        if a: flt.append(f"   if(Buf({_hname(a)}, 0, 1) < {float(f['min_adx'])}) return false;")
    if "event_blackout" in f:
        bk = f["event_blackout"]; flt.append("   datetime bc = EvBc(1);")
        flt.append(f"   if(EvNext(bc) <= {float(bk.get('before_min', 30))} || EvSince(bc) <= {float(bk.get('after_min', 30))}) return false;")
    flt += ["   return true;", "}"]
    atr_id = r.get("atr_ind", "atr")
    atr_expr = op(atr_id, "1") if r.get("sl_atr") else "0.0"
    src = TEMPLATE
    rep = {"NAME": name, "MAGIC": str(magic), "RISK": str(r.get("risk_pct", 1.0)), "SL": str(r.get("sl_atr") or 0.0),
           "TP": str(r.get("tp_atr") or 0.0), "MAXBARS": str(int(r.get("max_bars") or 0)),
           "FLAT": str(float(r.get("flatten_before_event_min") or 0)), "EVIMP": str(int(evc.get("min_impact", 3))),
           "EVDECAY": str(float(evc.get("decay_hours", 24))), "PERIOD_INPUTS": "\n".join(inputs), "TF": TFC[spec["timeframe"]],
           "BASE": sym[:3], "QUOTE": sym[3:6], "HANDLES": "\n".join(handles), "INITS": "\n".join(inits),
           "USE_EVENTS": "true" if use_ev else "false", "FILTERS": "\n".join(flt), "LONG_COND": allc(spec.get("entry_long")),
           "SHORT_COND": allc(spec.get("entry_short")), "LEXIT": allc(spec.get("exit_long")), "SEXIT": allc(spec.get("exit_short")),
           "ATR_EXPR": atr_expr, "REVERSE": "true" if r.get("allow_reverse", True) else "false",
           "FIXED_LOTS": str(float(r.get("fixed_lots") or 0.0)), "SYMBOL": sym}
    for k, v in rep.items(): src = src.replace(f"%%{k}%%", v)
    return src


TEMPLATE = r'''//+------------------------------------------------------------------+
//| %%NAME%% - generated by quantpipe from a JSON strategy spec       |
//| Test symbol: %%SYMBOL%%   Events CSV (if used): MQL5\Files\Common  |
//+------------------------------------------------------------------+
#property strict
#include <Trade\Trade.mqh>
CTrade trade;

input long   Magic = %%MAGIC%%;
input double RiskPct = %%RISK%%;
input double FixedLots = %%FIXED_LOTS%%;
input double SlAtr = %%SL%%;
input double TpAtr = %%TP%%;
input int    MaxBars = %%MAXBARS%%;
input double FlattenBeforeEventMin = %%FLAT%%;
input int    ServerTzMode = 1;          // 0 = fixed offset, 1 = NY-close broker (GMT+2 winter / GMT+3 summer)
input double ServerUtcOffsetH = 2.0;    // used when ServerTzMode = 0
input string EventsFile = "qp_events.csv";
input int    EvMinImpact = %%EVIMP%%;
input double EvDecayHours = %%EVDECAY%%;
%%PERIOD_INPUTS%%

const ENUM_TIMEFRAMES TF = %%TF%%;
const string EV_BASE = "%%BASE%%";
const string EV_QUOTE = "%%QUOTE%%";
const bool   USE_EVENTS = %%USE_EVENTS%%;
const bool   ALLOW_REVERSE = %%REVERSE%%;
datetime lastBar = 0;
%%HANDLES%%
datetime evT[]; double evZ[]; double evS[]; int evN = 0;

//--- time helpers -------------------------------------------------
datetime NthSunday(int year, int mon, int n)
{
   MqlDateTime d; d.year = year; d.mon = mon; d.day = 1; d.hour = 0; d.min = 0; d.sec = 0;
   datetime t = StructToTime(d); int cnt = 0;
   for(int i = 0; i < 31; i++)
   {
      TimeToStruct(t, d);
      if(d.mon != mon) break;
      if(d.day_of_week == 0) { cnt++; if(cnt == n) return t; }
      t += 86400;
   }
   return 0;
}
bool UsDst(datetime t)
{
   MqlDateTime d; TimeToStruct(t, d);
   return (t >= NthSunday(d.year, 3, 2) && t < NthSunday(d.year, 11, 1));
}
datetime ServerToUtc(datetime ts)
{
   if(ServerTzMode == 0) return ts - (datetime)(ServerUtcOffsetH * 3600.0);
   return ts - (UsDst(ts) ? 3 : 2) * 3600;
}
datetime EvBc(int shift) { return ServerToUtc(iTime(_Symbol, TF, shift - 1)); }   // close time (UTC) of bar[shift]

//--- events (read from CSV: epoch_utc,currency,impact,z) -----------
void LoadEvents()
{
   evN = 0;
   if(!USE_EVENTS) return;
   int h = FileOpen(EventsFile, FILE_READ | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE) { Print("events file missing: ", EventsFile, " err=", GetLastError()); return; }
   for(int i = 0; i < 4; i++) FileReadString(h);
   while(!FileIsEnding(h))
   {
      string se = FileReadString(h); string cur = FileReadString(h); string si = FileReadString(h); string sz = FileReadString(h);
      if(StringLen(se) == 0) continue;
      if((int)StringToInteger(si) < EvMinImpact) continue;
      double sgn = 0; if(cur == EV_BASE) sgn = 1; else if(cur == EV_QUOTE) sgn = -1; else continue;
      ArrayResize(evT, evN + 1); ArrayResize(evZ, evN + 1); ArrayResize(evS, evN + 1);
      evT[evN] = (datetime)StringToInteger(se); evZ[evN] = StringToDouble(sz); evS[evN] = sgn; evN++;
   }
   FileClose(h);
   PrintFormat("loaded %d events", evN);
}
double EvSince(datetime bc) { double b = 1e9; for(int i = 0; i < evN; i++) { if(evT[i] <= bc) b = (double)(bc - evT[i]) / 60.0; else break; } return b; }
double EvNext(datetime bc)  { for(int i = 0; i < evN; i++) if(evT[i] > bc) return (double)(evT[i] - bc) / 60.0; return 1e9; }
double EvSurprise(datetime bc)
{
   double s = 0; datetime lo = bc - (datetime)(EvDecayHours * 3600.0);
   for(int i = 0; i < evN; i++) { if(evT[i] > bc) break; if(evT[i] > lo) s += evZ[i] * evS[i]; }
   return s;
}
double EvInBar(datetime bc)
{
   datetime op = bc - PeriodSeconds(TF);
   for(int i = 0; i < evN; i++) { if(evT[i] >= bc) break; if(evT[i] >= op) return 1.0; }
   return 0.0;
}

//--- series helpers -------------------------------------------------
double Buf(int h, int b, int s) { double a[1]; if(CopyBuffer(h, b, s, 1, a) != 1) return EMPTY_VALUE; return a[0]; }
double HH(int period, int shift, int s) { double m = -DBL_MAX; for(int i = s + shift; i < s + shift + period; i++) { double v = iHigh(_Symbol, TF, i); if(v > m) m = v; } return m; }
double LL(int period, int shift, int s) { double m = DBL_MAX;  for(int i = s + shift; i < s + shift + period; i++) { double v = iLow(_Symbol, TF, i);  if(v < m) m = v; } return m; }

%%FILTERS%%

bool LongEntry()  { return (%%LONG_COND%%) && Filters(); }
bool ShortEntry() { return (%%SHORT_COND%%) && Filters(); }
bool LongExit()   { return (%%LEXIT%%); }
bool ShortExit()  { return (%%SEXIT%%); }

//--- sizing (same rules as the Python engine: floor to step, skip below min lot, margin cap) ----
double CalcLots(double slDist, bool buy)
{
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP), vmin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), vmax = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lots = FixedLots;
   if(lots <= 0)
   {
      double tv = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE), ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
      if(slDist <= 0 || ts <= 0 || tv <= 0) return 0;
      lots = AccountInfoDouble(ACCOUNT_EQUITY) * RiskPct / 100.0 / (slDist / ts * tv);
   }
   lots = MathFloor(lots / step + 1e-9) * step; if(lots > vmax) lots = vmax;
   if(lots < vmin - 1e-12) return 0;
   double price = buy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID), margin = 0;
   if(OrderCalcMargin(buy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, _Symbol, lots, price, margin) && margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.9)
   {
      lots = MathFloor(lots * AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.9 / margin / step) * step;
      if(lots < vmin - 1e-12) return 0;
   }
   return NormalizeDouble(lots, 2);
}

bool FindPos(ulong &ticket, long &type, datetime &opened)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(tk == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol && PositionGetInteger(POSITION_MAGIC) == Magic)
      { ticket = tk; type = PositionGetInteger(POSITION_TYPE); opened = (datetime)PositionGetInteger(POSITION_TIME); return true; }
   }
   return false;
}

void OpenTrade(bool buy)
{
   double a = %%ATR_EXPR%%;
   double slDist = 0, tpDist = 0;
   if(SlAtr > 0) { slDist = MathMax(SlAtr * a, SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point); if(TpAtr > 0) tpDist = MathMax(TpAtr * a, SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point); }
   double lots = CalcLots(slDist, buy);
   if(lots <= 0) { Print("skip: lot below min / margin"); return; }
   double px = buy ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl = (slDist > 0) ? NormalizeDouble(buy ? px - slDist : px + slDist, _Digits) : 0;
   double tp = (tpDist > 0) ? NormalizeDouble(buy ? px + tpDist : px - tpDist, _Digits) : 0;
   trade.SetExpertMagicNumber(Magic);
   if(buy) trade.Buy(lots, _Symbol, 0, sl, tp, "qp"); else trade.Sell(lots, _Symbol, 0, sl, tp, "qp");
}

int OnInit()
{
%%INITS%%
   LoadEvents();
   return INIT_SUCCEEDED;
}

void OnTick()
{
   datetime t0 = iTime(_Symbol, TF, 0);
   if(t0 == lastBar) return;
   lastBar = t0;
   if(Bars(_Symbol, TF) < 300) return;
   bool le = LongEntry(), se = ShortEntry();
   ulong tk; long ty; datetime opened; bool reenter = true;
   if(FindPos(tk, ty, opened))
   {
      bool isLong = (ty == POSITION_TYPE_BUY);
      bool opp = isLong ? se : le, ex = isLong ? LongExit() : ShortExit(), close = false;
      if(opp || ex) close = true;
      if(MaxBars > 0 && (int)((TimeCurrent() - opened) / PeriodSeconds(TF)) >= MaxBars) { close = true; reenter = false; }
      if(FlattenBeforeEventMin > 0 && USE_EVENTS && EvNext(EvBc(0)) <= FlattenBeforeEventMin) { close = true; reenter = false; }
      if(close) trade.PositionClose(tk);
      if(!(opp && ALLOW_REVERSE && reenter)) return;
   }
   if(le && !se) OpenTrade(true); else if(se && !le) OpenTrade(false);
}
'''
