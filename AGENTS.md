# Agent Instructions

This repository is the build target for the Imperial live-paper trading agent.

## Required Orientation

Before making changes, read:

1. `MISSION.md`
2. `README.md`
3. Any existing local config, ledgers, reports, or memory files if they are later added.

## Operating Mode

- Default mode is `live-paper-only`.
- Do not place live orders.
- Do not sign transactions.
- Do not move funds.
- Do not spend paid API budget without explicit human approval.
- Do not create historical simulated trades and count them as paper results.
- Paper trades must be generated from live market snapshots before outcomes occur.

## Build Priorities

1. Read-only data adapters.
2. Provenance and source health.
3. Live paper order logging.
4. Forward outcome evaluation.
5. Memory and self-improvement ledgers.
6. Cron-ready commands.

## Safety

Any live trading, wallet access, signing path, leverage increase, paid API spend, or weakening of risk controls requires explicit human approval.

