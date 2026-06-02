# Agent Instructions

This repository is the build target for the Imperial live-paper trading agent.

## Required Orientation

Before making changes, read:

1. `MISSION.md` — current mission spec (24-hour live-paper trial)
2. `README.md` — project overview and quick commands
3. `config/run.yaml` — schedule, equity, and run parameters
4. `config/risk.yaml` — risk limits, cancel rules, leverage bounds

## Operating Mode

- Default mode is `live-paper-only`.
- Do not place live orders.
- Do not sign transactions.
- Do not move funds.
- Do not spend paid API budget without explicit human approval.
- Do not create historical simulated trades and count them as paper results.
- Paper trades must be generated from live market snapshots before outcomes occur.

## Current Mission: 24-Hour Live-Paper Trial

The agent is configured for hourly paper trading to accumulate performance data over 24 cycles.

### Key Parameters

- **Equity**: 1000 USDC
- **Max concurrent trades**: 4
- **Max candidates per scan**: 3
- **Cancel timeout**: 45 minutes (matches hourly cycle)
- **No 22:00 hard exit** (not applicable to hourly schedule)
- **Schedule**: Hourly, every hour on the hour (`0 * * * *`)

### Learning Observer, Not Timid Trader

The agent reads signal outcome stats to learn which signals perform well, but this learning is **informational only**:
- Signal weights in `scoring.py` are FIXED and must never be adjusted by outcomes.
- Position sizing must never be reduced due to prior losses.
- Every scan treats the opportunity fresh with full aggression.
- Prior outcomes are logged and reported but do not affect conviction or sizing.

### Auto-Evaluate Before Each Scan

Before placing new orders, the scan loop evaluates any open orders from the previous hour:
- Fetches current mark prices for open orders
- Evaluates fill status via `evaluate_fill()`
- Computes outcomes (R, MAE, MFE) for closed orders
- Writes to `outcomes.csv` and `signal_outcomes.csv`
- Updates `mission_state.json`

This runs inline at the start of `_run_live_paper()`, not as a separate mode.

## Build Priorities

1. Config updates for hourly trial (run.yaml, risk.yaml)
2. Auto-evaluate step in scan loop
3. Cron and trial management scripts
4. Trial dashboard for real-time monitoring
5. Signal learning output in reports
6. Trial stop + final assessment

## Safety

Any live trading, wallet access, signing path, leverage increase, paid API spend, or weakening of risk controls requires explicit human approval.

Cron installation requires explicit human approval — never auto-install cron entries.

