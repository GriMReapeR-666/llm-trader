# Artifacts - Runtime & Reports

Gitignored. Regenerated each run.

- 	est_outputs/ — moved from root 	est_*.json (25 files)
- roker_replay/ — historical tick replay caches
- eports/ — backtest / audit reports
- metrics/ — prometheus metrics snapshots
- *.json — alpha stability, ensemble weights, trial ledger, caches (ignored except committed samples)

Do not commit *.db or *_state.json here; they live at root and are gitignored.

