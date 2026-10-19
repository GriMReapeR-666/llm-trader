# The calendar cannot supply release actuals — verified 2026-10-12

## Summary

The ForexFactory calendar feed **structurally cannot** provide the release
surprises (`actual` vs `forecast`) that the causal-validation pipeline is
built to consume. This is a property of the feed, not a bug in our code, and
no amount of fixing our code changes it.

## What was verified

Probed every plausible faireconomy endpoint:

| Endpoint | Result |
|---|---|
| `ff_calendar_thisweek.json` | 200, 108 rows |
| `ff_calendar_nextweek.json` | 404 |
| `ff_calendar_lastweek.json` | 404 |
| `ff_calendar_prevweek.json` | 404 |
| `ff_calendar_previousweek.json` | 404 |
| `ff_calendar_last_week.json` | 404 |
| `ff_calendar_today.json` | 404 |
| case variants (`thisWeek`, `lastWeek`, `nextWeek`) | 404 |

There is **one** window and no archive.

Within that single window, on 2026-10-12T21:49Z, of 108 rows:

```
events whose scheduled time had ALREADY PASSED : 7
  ... of those, actual populated : 0
  ... of those, forecast populated : 0
HIGH-impact events already passed : 0
```

Rows already elapsed still carry `actual=None`. The feed is **forward-looking
only** — it never backfills a release outcome, at any point during the week.

## Why this blocks the causal layer

`MacroGraph.schedule_event_reaction` (`intelligence/macro_graph.py:463`) gates on:

```python
actual = self._number(event.get("actual"))
if (actual is None
        or (forecast is None and previous is None)
        or event_time is None
        or event_time > now
        or anchor is None or anchor <= 0):
    return None
```

- **Upcoming event:** forecast present, `actual` absent → gate returns `None`.
- **Elapsed event:** every numeric field absent → gate returns `None`.

The gate cannot pass for this feed in either state. So no tracker is ever
created, no `event_reaction` is ever completed, and `AutonomousCausalValidator`
(which reads only `event_reactions`) never accumulates samples. The two
reactions that do exist came from a different path, not this feed.

## Related: the 81 non-EVENT_CLASS edges

Separately, `macro_observations` (448k rows) is **not** a usable substitute.
Re-derive these numbers with the auditor:

```python
from intelligence.observation_provenance import audit
audit("trading_state.db")
```

```
total_series            : 65
total_rows              : 443,842
effective points        : 329,784
sentinel_series         : 42   (never vary; value=101.4 across AAR / AISHUB /
                                  CREDIT_CARD / ENTSO-E / SATELLITE)
low_sample_series       : 18   (DGS10, VIXCLS, T10Y2Y ... n=14-18 real points)
usable_series           : 5    (realtime_fx, energy_futures_ticks,
                                  fed_sofr_futures, options_vol_surface,
                                  cftc_cot_weekly)

edge_coverage:
  total_edges                 102
  event_class_edges            21
  non_event_class_edges        81
  observation_validatable       0   <-- no edge has TWO measurable endpoints
  source_measurable_target_not 10
  no_measurable_endpoint       71
  verdict               NO_OBSERVATION_EVIDENCE_AVAILABLE
```

Daily FRED series carry ~6,300 rows but only **14-18 distinct timestamps** --
the backfill re-ingested the same daily value ~350x. Row count is not sample
size. Calendar observations (3,697) carry **zero** surprise values.

Validating causal edges on this would approve them against a constant, or
against a 14-point sample where no significance threshold is meaningful.
`intelligence/observation_provenance.py` exists so that check is explicit
rather than assumed.

## What would actually fix it

A source that publishes **release outcomes**, in preference order:

1. **ALFRED** (FRED's Archival Federal Reserve Economic Data) — the canonical
   point-in-time vintage store. Serves the value *as known on release day*,
   which is exactly what the spec's "Point-in-Time Vintage Data" requirement
   asks for. Free, needs an API key. Already have `fredapi` as a dependency.
2. Any commercial calendar feed with an actuals endpoint (not scraped).

ALFRED is the right answer and it is a contained build: a provider mirroring
`data/fred_backtest`/`data/eia_provider` shapes, feeding the existing
`macro_events` → `event_reaction_trackers` → `event_reactions` path.

For the 81 macro-state edges the fix is *different*: they need a real price
series on **both** endpoints (e.g. an actual EURUSD and a real GOLD series, so
`DXY --INVERSE_PRICES--> GOLD` becomes testable). No validator code substitutes
for the missing measurement.

## Do not

- Point `FF_CALENDAR_URL` at a history endpoint; there isn't one.
- Backfill edges from `macro_observations`; 42 series are constants and the
  rest have n=14-18. Use `observation_provenance.audit()` to check first.
