//+------------------------------------------------------------------+
//| ExportCalendar.mq5 - dumps the broker's MT5 economic calendar     |
//| Run as a Script on any chart. Output: MQL5\Files\qp_calendar_raw.csv (FILE_COMMON)
//| Times are SERVER time -> import in Python with server_tz="ny_close" (or fixed:+N).
//| Use first-release values: this exports the value as stored now - check the 'revised' caveat in README.
//+------------------------------------------------------------------+
#property script_show_inputs
input datetime From = D'2018.01.01';
input datetime To   = D'2026.01.01';
input string   Out  = "qp_calendar_raw.csv";

double V(long v) { return (v == LONG_MIN) ? EMPTY_VALUE : v / 1000000.0; }

void OnStart()
{
   MqlCalendarValue vals[];
   if(!CalendarValueHistory(vals, From, To)) { Print("CalendarValueHistory failed: ", GetLastError()); return; }
   int h = FileOpen(Out, FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON, ',');
   if(h == INVALID_HANDLE) { Print("cannot open ", Out); return; }
   FileWrite(h, "time", "currency", "event", "impact", "actual", "forecast", "previous");
   for(int i = 0; i < ArraySize(vals); i++)
   {
      MqlCalendarEvent ev; if(!CalendarEventById(vals[i].event_id, ev)) continue;
      MqlCalendarCountry c; if(!CalendarCountryById(ev.country_id, c)) continue;
      string imp = (ev.importance == CALENDAR_IMPORTANCE_HIGH) ? "high" : (ev.importance == CALENDAR_IMPORTANCE_MODERATE ? "medium" : "low");
      string act = vals[i].HasActualValue() ? DoubleToString(vals[i].GetActualValue(), 6) : "";
      string fc  = vals[i].HasForecastValue() ? DoubleToString(vals[i].GetForecastValue(), 6) : "";
      string pv  = vals[i].HasPreviousValue() ? DoubleToString(vals[i].GetPreviousValue(), 6) : "";
      FileWrite(h, TimeToString(vals[i].time, TIME_DATE | TIME_MINUTES), c.currency, ev.name, imp, act, fc, pv);
   }
   FileClose(h);
   PrintFormat("exported %d calendar rows to Common\\Files\\%s", ArraySize(vals), Out);
}
