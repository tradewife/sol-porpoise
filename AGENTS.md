# Agent Instructions

This repository is **Sol Porpoise** -- a live-paper crypto perps trading agent executing on Phoenix Perps via Vulcan.

## Required Orientation

Before making changes, read:

1. `README.md` -- project overview, architecture, quick commands
2. `config/run.yaml` -- schedule, equity, and run parameters
3. `config/risk.yaml` -- risk limits, cancel rules, leverage bounds
4. `config/ai_agent.yaml` -- AI agent config, skills, bridge settings

## Operating Mode

- Default mode is `live-paper-only`.
- Paper trades execute on Phoenix Perps via Vulcan with real market prices.
- Do not place live orders.
- Do not sign transactions.
- Do not move funds.
- Do not spend paid API budget without explicit human approval.
- Do not create historical simulated trades and count them as paper results.
- Paper trades must be generated from live market snapshots before outcomes occur.

## Architecture Overview

Two parallel accounts run hourly via `cron_hourly.sh`:

- **Deterministic** (`accounts/deterministic/`) -- 14-step scan: data fetch -> 9 signals -> scoring -> playbooks -> risk sizing -> Vulcan execution
- **AI** (`accounts/ai/`) -- data fetch -> prompt + skills -> `droid exec` (GLM-5.1) -> validate prompt-bound response -> risk sizing -> Vulcan execution

Both fall back to synthetic paper_orders.csv when Vulcan is unavailable.

### Vulcan Execution

When `vulcan` CLI is installed, trades execute via `adapters/vulcan.py`:
- Paper state files at `accounts/<id>/data/vulcan-paper-state.json` (isolated per account)
- Market fills at real Phoenix prices with real fees
- TP/SL triggers persist across sessions
- Duplicate position prevention (same symbol check)

### AI Agent Bridge

`scripts/ai_delegate_agent.sh` calls `droid exec` with:
- Model: `custom:GLM-5.1-[Z.AI-Coding-Plan]---Openai-0`
- Reasoning: high
- Reads prompt file, outputs JSON response
- Response must include matching `prompt_id`

### Trading Skills

10 repo-local skills loaded at runtime from `skills/<name>/SKILL.md`. Add new skills by creating the SKILL.md and listing in `config/ai_agent.yaml` under `skills.enabled`.

### Hawk Breakout Integration

The hawk breakout pipeline adds deterministic breakout detection to the AI paper trading path:

- **`engine/hawk_breakout.py`** -- Deterministic 7-day breakout signal with Smart Money tilt gating and 0-9 scoring (Senpi Hawk v1.0.0). Runs in `ai-paper` mode only, before prompt building.
- **`skills/market-structure-context/SKILL.md`** -- AI skill that teaches the agent to classify HTF regime (trending_up, trending_down, ranging, compression, unknown) from the Market Data table, produce alignment scores, and gate breakouts through structureConfirmed/partial/rejected.
- **`skills/hawk-breakout/SKILL.md`** -- AI skill that teaches the agent to interpret pre-computed hawk signals in the prompt, including hard veto rules and scoring interpretation.
- Both skills are listed in `config/ai_agent.yaml` under `skills.enabled` at positions 3 and 4.
- **`config/strategy.yaml`** -- Holds hawk breakout parameters (lookback, SM tilt thresholds, scoring weights, volume multiplier). The module works without this file via Python defaults.
- **Current limitation:** Hawk signals currently return "none" because only a single mark-price snapshot is available. The 7-day breakout gate requires 168 hourly candle closes, which are not yet wired in. The gate fails gracefully (no signal produced, no crash).

## Learning Observer, Not Timid Trader

- Signal weights in `scoring.py` are FIXED and must never be adjusted by outcomes.
- Position sizing must never be reduced due to prior losses.
- Every scan treats the opportunity fresh with full aggression.
- Prior outcomes are logged and reported but do not affect conviction or sizing.

## Safety

Any live trading, wallet access, signing path, leverage increase, paid API spend, or weakening of risk controls requires explicit human approval.

Cron installation requires explicit human approval -- never auto-install cron entries.
