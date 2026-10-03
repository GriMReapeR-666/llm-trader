# Local-secret and trading-state policy

Keep credentials only in an untracked `.env` injected by the operator.  Do not
place MT5 passwords, API keys, or account numbers in source code, examples,
or commits.  Rotate any credential that was previously committed or shared.

`trading_state.db` is the local durable journal and safety-state database. It
contains proposal and order metadata and must remain local.  Use an encrypted,
access-controlled database before operating across machines.
