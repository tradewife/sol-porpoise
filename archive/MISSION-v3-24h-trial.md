# Mission: Imperial Agent — 24-Hour Live-Paper Trial

Previous mission specs archived at `archive/MISSION-v2-activation.md`.

## Purpose

Run the Imperial Agent in live-paper mode for 24 hours on an hourly cron schedule. The goal is to accumulate ~24 hourly scan cycles producing paper trade setups with entry, stop-loss, and take-profit levels. After 24 hours we will have enough data to assess:

- **Signal quality**: Which of the 9 signal components produce winning trades?
- **Setup hit rate**: Which playbook types (breakout, fade, momentum, etc.) work?
- **System expectancy**: Is the average R per trade positive?
- **Drawdown profile**: What's the worst peak-to-trough R over the trial?

## Key Design Decisions

1. **Hourly scans** — Cron fires every hour on the hour. Each scan fetches live data, extracts signals, generates playbooks, and writes paper orders.
2. **Auto-evaluate before each scan** — Before placing new orders, the agent evaluates any open orders from the previous hour. This gives us clean outcome data on every trade.
3. **Learning observer, not timid trader** — The agent should read signal outcome stats to learn which signals are performing well, but this learning must NOT reduce position sizing or make the agent more conservative. Every scan treats the opportunity fresh with full aggression. Prior outcomes are information only.
4. **4 concurrent trades** — Up to 4 paper orders can be open at once, giving us more data per cycle.
5. **1000 USDC equity** — Sized to allow multiple concurrent positions with 9-12x leverage.
6. **No hard exit at 22:00** — The 22:00 hard exit rule is designed for 3x/day schedules. For hourly runs, we cancel stale orders via the 45-minute timeout instead.

## What Needs to Change

### 1. Config Updates

**File**: `config/run.yaml`

Changes:
- `schedule.scan_times`: Replace with hourly schedule (every hour, 00-23)
- `schedule.cron_scan`: Update to `0 * * * *` (hourly)
- `schedule.outcome_eval_time`: Remove or set to hourly as well
- `account.equity`: Change from 100 to 1000
- `account.max_open_trades`: Change from 2 to 4
- `run.max_candidates`: Change from 2 to 3 (more candidates per scan)

**File**: `config/risk.yaml`

Changes:
- `equity`: Change from 100 to 1000
- `cancel_rules.timeout_minutes`: Change from 90 to 45 (orders older than 45 minutes are stale for hourly cycle)
- `cancel_rules.hard_exit_time`: Remove or disable (not applicable to hourly schedule)
- `portfolio.max_open_trades`: Change from 2 to 4

### 2. Scan Loop: Auto-Evaluate Before New Orders

**File**: `engine/run_scan.py`

Add a pre-scan evaluation step at the start of `_run_live_paper()`:

```
Step 0a: If open_paper_orders is non-empty, run evaluate-outcomes logic inline:
  - Fetch current mark prices for each open order
  - Build candle from entry to current price
  - Run evaluate_fill() for each order
  - If filled + closed: compute outcome, write to outcomes.csv
  - If cancel triggered: write cancel outcome
  - Update mission_state.json (remove resolved, keep in-trade)
  - Log evaluation results
```

This is NOT a separate mode call — it's inline at the start of the live-paper scan, before any new data fetching or signal extraction happens. The agent evaluates yesterday's (or last hour's) orders before placing new ones.

### 3. Signal Outcome Learning (Informational Only)

**File**: `engine/signals.py` or `engine/run_scan.py`

Before signal extraction, read `signal_outcomes.csv` stats (if available) and log them. These stats are used for:

- Report section output ("Signal performance: funding_stretch hit rate 62%, oi_delta 45%")
- Future recommendation engine (weekly review already does this)
- **NOT** for adjusting signal weights, confidence, or position sizing

The agent must never let a losing streak on one signal reduce its conviction on the next occurrence. All 9 signals retain their fixed weights from `scoring.py` regardless of historical performance.

### 4. Hourly Cron Script

**File**: `scripts/cron_hourly.sh` (new)

A single cron-ready script that runs the full hourly cycle:
1. Run evaluate-outcomes for any open orders
2. Run live-paper scan for new candidates
3. Log completion with timestamp

The cron entry (not auto-installed, requires human approval):
```
0 * * * * /home/kt/imperial-agent/scripts/cron_hourly.sh >> /home/kt/imperial-agent/data/cron.log 2>&1
```

### 5. Trial Start/Stop Scripts

**File**: `scripts/trial_start.sh` (new)
- Backs up current config to `data/trial_config_backup/`
- Applies hourly trial config
- Resets ledgers (or archives existing data)
- Runs initial dry-run to verify config
- Prints cron line for human to install

**File**: `scripts/trial_stop.sh` (new)
- Removes cron entry
- Runs final outcome evaluation
- Runs weekly review on trial data
- Restores original config from backup
- Prints trial summary (trade count, expectancy, hit rate)

### 6. Trial Dashboard

**File**: `engine/trial_dashboard.py` (new)

A summary command that can be run at any time during the 24-hour trial:
```
.venv/bin/python -m engine.trial_dashboard
```

Output:
- Hours elapsed / hours remaining
- Total scans completed
- Paper orders placed (count, by symbol, by setup type)
- Orders filled / cancelled / still open
- Outcomes computed: win count, loss count, expectancy R
- Best trade / worst trade
- Per-signal hit rate
- Per-setup-type hit rate
- Current open positions with unrealized P&L

## Operating Constraints

- Mode remains `live-paper-only` throughout. No live trading.
- All paper trades are generated from live market snapshots.
- The agent does not sign transactions or move funds.
- Signal learning is informational only — never reduces aggression.

## Milestones

### Milestone A: Config + Auto-Evaluate + Cron Script

**Deliver**: Updated configs (run.yaml, risk.yaml) for hourly operation with 1000 USDC. Auto-evaluate step wired into scan loop. `scripts/cron_hourly.sh` created. `scripts/trial_start.sh` and `scripts/trial_stop.sh` created.

**Validation**: Run `./scripts/trial_start.sh` then `./scripts/cron_hourly.sh` manually. Verify config is applied, evaluate step runs, scan completes with report generated.

### Milestone B: Trial Dashboard + Learning Output

**Deliver**: `engine/trial_dashboard.py` with live summary. Signal outcome stats logged in report. Learning output visible but not affecting trade decisions.

**Validation**: Run dashboard after a few manual hourly scans. Verify it shows correct counts and metrics.

### Milestone C: 24-Hour Trial Execution

**Deliver**: Install cron (with human approval), run for 24 hours, collect results, produce final assessment.

**Validation**: After 24 hours, run `./scripts/trial_stop.sh` and verify final summary shows meaningful data (24+ scans, multiple paper trades, outcome metrics).

## Success Criteria

- Agent runs 24 hourly cycles without crashing
- At least 15 out of 24 cycles produce either a paper trade or a documented no-trade reason
- At least 8 paper orders are generated across the trial
- Outcome evaluation fills in R-values for at least 50% of closed orders
- Weekly review / trial dashboard produces actionable signal quality data
