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

7 repo-local skills loaded at runtime from `skills/<name>/SKILL.md`. Add new skills by creating the SKILL.md and listing in `config/ai_agent.yaml` under `skills.enabled`.

## Learning Observer, Not Timid Trader

- Signal weights in `scoring.py` are FIXED and must never be adjusted by outcomes.
- Position sizing must never be reduced due to prior losses.
- Every scan treats the opportunity fresh with full aggression.
- Prior outcomes are logged and reported but do not affect conviction or sizing.

## Safety

Any live trading, wallet access, signing path, leverage increase, paid API spend, or weakening of risk controls requires explicit human approval.

Cron installation requires explicit human approval -- never auto-install cron entries.
