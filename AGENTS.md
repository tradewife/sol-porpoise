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

- **Deterministic** (`accounts/deterministic/`) -- 14-step scan: data fetch -> 10 signals -> scoring -> playbooks -> risk sizing -> Vulcan execution
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

11 repo-local skills loaded at runtime from `skills/<name>/SKILL.md`. Add new skills by creating the SKILL.md and listing in `config/ai_agent.yaml` under `skills.enabled`.

### Hawk Breakout Integration

The hawk breakout pipeline adds deterministic breakout detection to the AI paper trading path:

- **`engine/hawk_breakout.py`** -- Deterministic 7-day breakout signal with Smart Money tilt gating and 0-9 scoring (Senpi Hawk v1.0.0). Runs in `ai-paper` mode only, before prompt building. Candle history (168 hourly + 42 four-hourly) wired from Hyperliquid API -- produces live 0-9 scores.
- **`skills/market-structure-context/SKILL.md`** -- AI skill that teaches the agent to classify HTF regime (trending_up, trending_down, ranging, compression, unknown) from the Market Data table, produce alignment scores, and gate breakouts through structureConfirmed/partial/rejected.
- **`skills/hawk-breakout/SKILL.md`** -- AI skill that teaches the agent to interpret pre-computed hawk signals in the prompt, including hard veto rules and scoring interpretation.
- Both skills are listed in `config/ai_agent.yaml` under `skills.enabled` at positions 3 and 4.
- **`config/strategy.yaml`** -- Holds hawk breakout parameters (lookback, SM tilt thresholds, scoring weights, volume multiplier). The module works without this file via Python defaults.
- **Candle data** is fetched from Hyperliquid API (`candleSnapshot`), cached at `accounts/<id>/data/candles_SOL_1h.json` with 55-minute TTL, and wired into both deterministic and AI scan paths.

### Data Layer

- **Hyperliquid Direct API** (`adapters/hyperliquid.py`) -- Replaces Phantom MCP. Direct HTTP POST to `api.hyperliquid.xyz/info`. Provides markets, funding, OI, L2 orderbook (book_imbalance_ratio), and candle data.
- **Hyperdash GraphQL** (`adapters/hyperdash.py`) -- Whale cohort directional bias. Used in AI path only.
- **Dextrabot** (`adapters/dextrabot.py`) -- Whale wallet intelligence via direct JSON API (`dextradata.nftinit.io`). Includes `smart_money`, `whale_unlabeled`, and `roi_whale` tiers.
- **Imperial API** (`adapters/imperial.py`) -- Mark prices, funding rates, volume, OI, depth (primary data source for deterministic path, fallback for AI path).

### Signal Pipeline

- 10 signal extractors in `engine/signals.py`: funding_stretch, oi_delta, basis, liquidity_magnet, session_structure, whale_evidence, dex_perp_lag, volatility, catalyst, **book_imbalance**
- Weights in `engine/scoring.py` sum to 1.0 and are FIXED (never adjusted by outcomes)

## Learning Observer, Not Timid Trader

- Signal weights in `scoring.py` are FIXED and must never be adjusted by outcomes.
- Position sizing must never be reduced due to prior losses.
- Every scan treats the opportunity fresh with full aggression.
- Prior outcomes are logged and reported but do not affect conviction or sizing.

## Safety

Any live trading, wallet access, signing path, leverage increase, paid API spend, or weakening of risk controls requires explicit human approval.

Cron installation requires explicit human approval -- never auto-install cron entries.
