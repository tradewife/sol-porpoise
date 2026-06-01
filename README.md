# Imperial Agent

Imperial Agent is a mission workspace for building a live-paper crypto perps research and trading agent.

The current mission is defined in `MISSION.md`: a Hyperliquid and Solana perps alpha Droid that scans live markets, creates timestamped non-executing paper orders, evaluates only forward outcomes, and persists evidence for self-improvement.

## Current Status

- Mission spec exists.
- Default mode is `live-paper-only`.
- No live trading is enabled.
- No adapters, ledgers, reports, or cron scripts have been scaffolded yet.

## Key Rule

Paper results must come from live forward paper trades only. Historical data may be used for context and feature calculation, but historical replay, synthetic fills, and backtests cannot count as paper-trading outcomes or promotion evidence.

## Next Build Step

Start with Milestone 1 from `MISSION.md`: create the minimal config, ledger schemas, memory files, and a dry plumbing command that can produce a timestamped `no_trade` report without generating paper outcomes.

