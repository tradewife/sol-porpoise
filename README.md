# Sol Porpoise

Live-paper crypto trading agent for Solana perpetual futures. Two parallel paper accounts -- deterministic and AI -- execute via [Vulcan](https://github.com/Ellipsis-Labs/vulcan-cli) on [Phoenix Perps](https://phoenix.trade/) with real market prices, TP/SL triggers, and live PnL tracking.

## Current Status

**Mode: `live-paper-only`** -- no live trading, no signing, no fund movement.

**790 tests pass.** A cron job fires every hour on the hour, running two independent paper trading accounts in parallel on Phoenix Perps:

1. **Deterministic account** (`accounts/deterministic/`) -- 14-step scan loop with fixed signal weights, 10 signal extractors, 7 playbook types. Executes paper trades via Vulcan on Phoenix.
2. **AI account** (`accounts/ai/`) -- AI reasoning via repo-local skills + Droid/Hermes agent delegation (GLM-5.1). Builds enriched prompt, calls `droid exec`, validates prompt-bound response, executes via Vulcan. Falls closed to no-trade when no agent response.

Both accounts have isolated Vulcan paper state with real fills, fees, TP/SL triggers, and live PnL against Phoenix market prices.

## Account Structure

```
accounts/
  deterministic/
    ledgers/               # paper_orders.csv, outcomes.csv, signal_outcomes.csv
    reports/               # timestamped markdown reports
    memory/                # mission_state.json
    data/                  # vulcan-paper-state.json, prompt/log files

  ai/
    ledgers/               # (same schema)
    reports/
    memory/
    data/                  # vulcan-paper-state.json, ai_prompt.txt, ai_request.json, ai_response.json
```

Compare both accounts: `python -m engine.trial_dashboard --all`

## Architecture

### Execution Layer
- **Vulcan adapter** (`adapters/vulcan.py`) -- wraps `vulcan` CLI for programmatic paper trading on Phoenix Perps. Multi-account isolation via per-account state files. Market orders, TP/SL triggers, position management, live PnL.
- Falls back to synthetic `paper_orders.csv` when Vulcan is not installed.

### Data Sources
- **Imperial API** (`adapters/imperial.py`) -- mark prices, funding rates, volume, OI, depth
- **Flash Trade MCP** (`adapters/flash_trade.py`) -- Solana perps market data
- **Hyperliquid Direct API** (`adapters/hyperliquid.py`) -- direct HTTP POST to `api.hyperliquid.xyz/info`. Provides markets, funding rates, OI, L2 orderbook (book_imbalance_ratio), and candle data (168 hourly + 42 four-hourly). Cached with 55-min TTL.
- **Hyperdash GraphQL** (`adapters/hyperdash.py`) -- whale cohort directional bias from Hyperdash GraphQL API
- **Dextrabot** (`adapters/dextrabot.py`) -- whale intelligence via direct JSON API (`dextradata.nftinit.io`). Includes `roi_whale` tier. No HTML scraping.
- **Kukapay News** (`adapters/kukapay.py`) -- crypto news sentiment for catalyst signal (deterministic only)
- **Twitter CT Intel** (`adapters/twitter_news.py`) -- twitter-cli search for CT sentiment (AI agent only)

### Config (`config/`)
- `run.yaml` -- mode, hourly schedule, 1000 USDC equity, max 4 concurrent, max 3 candidates
- `risk.yaml` -- equity 1000, max risk 20%, leverage 9-12x, 45-min cancel timeout
- `ai_agent.yaml` -- MCP sources, agent_file response provider, skills config, bridge command (droid exec + GLM-5.1)
- `strategy.yaml` -- hawk breakout parameters (lookback, SM tilt thresholds, scoring, volume multiplier)
- `venues.yaml` -- Imperial API + Solana perp venues (Jupiter, Flash Trade, Phoenix, GM Trade)

### Engine -- Deterministic Pipeline (`engine/`)
- `signals.py` -- 10 signal extractors (funding_stretch, oi_delta, basis, liquidity_magnet, session_structure, whale_evidence, dex_perp_lag, volatility, catalyst, book_imbalance)
- `scoring.py` -- Fixed weighted scoring (10 components, weights sum to 1.0, never adjusted by outcomes). `book_imbalance` at 0.08, `whale_evidence` 0.07, `basis` 0.05.
- `playbooks.py` -- 7 setup types (breakout, fade, vwap_reclaim, funding_fade, momentum_continuation, liquidity_sweep, lvn_rejection)
- `risk.py` -- Position sizing (risk_usd / ATR stop_distance, leverage 9-12x cap)

### Engine -- AI Pipeline (`engine/`)
- `hawk_breakout.py` -- Deterministic 7-day breakout signal with Smart Money tilt gating and 0-9 scoring (Senpi Hawk v1.0.0). Runs in ai-paper mode only. Candle history (168 hourly + 42 four-hourly) wired from HL API -- produces live 0-9 scores.
- `mcp_data.py` -- Builds enriched AI prompt from live market data + active skills. Includes hawk signals section (extract_sm_tilt, format_hawk_prompt_section). Requires prompt-bound JSON response.
- `ai_agent.py` -- Parses AI responses, validates prompt_id binding, validates candidates (stop side, R:R >= 2, ATR floor, evidence/risk_notes/data_gaps)
- `skills.py` -- Loads repo-local trading skills from `skills/<name>/SKILL.md`
- `run_scan.py` -- 4 modes: `plumbing-dry-run`, `live-paper`, `evaluate-outcomes`, `ai-paper`. Computes hawk breakout signals before prompt building in ai-paper mode. Both trading modes try Vulcan first, fall back to synthetic.

### AI Trading Skills (`skills/`)
Expandable prompt modules controlled by `config/ai_agent.yaml`. Add new skills as `skills/<name>/SKILL.md`. Currently 11 skills enabled:

- `core-trader-mandate` -- Elite perps trader posture, urgency + selectivity, no invented data
- `hyperliquid-microstructure` -- Funding stretch, OI expansion/contraction, setup patterns
- `market-structure-context` -- HTF regime classification, alignment scoring, setup type selection, evidence tagging. Gates breakouts through structureConfirmed/partial/rejected lens.
- `hawk-breakout` -- Interprets pre-computed Hawk breakout signals (Senpi Hawk v1.0.0). 7-day high/low breakout + SM tilt gate + 0-9 scoring. AI-readable signal interpretation and hard veto rules.
- `solana-perps-context` -- Solana-native venue data, pool utilization, SOL beta behavior
- `whale-leaderboard-intel` -- Whale classification, no copy-trade, multi-source overlap
- `risk-execution-rails` -- Hard constraints: equity, risk %, leverage, R:R minimums
- `provenance-auditor` -- Evidence tags, risk_notes, data_gaps per trade
- `outcome-learning` -- Informational prior outcomes, never adjust weights or sizing
- `twitter-ct-intel` -- CT sentiment as soft context (AI agent only, never scored)
- `orderbook-liquidity` -- L2 orderbook imbalance interpretation, bid/ask wall detection, liquidity gap analysis

### Scripts (`scripts/`)
- `cron_hourly.sh` -- Hourly cron entry point for both accounts
- `ai_delegate_agent.sh` -- Bridge: calls `droid exec` with GLM-5.1 high reasoning after prompt is written
- `run_scan.sh`, `trial_start.sh`, `trial_stop.sh` -- CLI wrappers

### Tests
790 tests covering all validation contracts, engine modules, cross-module pipelines, account isolation, AI parsing, MCP data, skills loading, Twitter adapter, hawk breakout signals, book imbalance signal, Hyperliquid adapter, Hyperdash adapter, Dextrabot JSON API, and end-to-end trial cycles.

## Quick Commands

```bash
# Deterministic paper scan
./scripts/run_scan.sh --mode live-paper --account deterministic

# AI paper scan (builds prompt, calls droid exec, executes via vulcan)
./scripts/run_scan.sh --mode ai-paper --account ai

# Plumbing dry run (no network)
./scripts/run_scan.sh --mode plumbing-dry-run

# Dashboard (both accounts)
.venv/bin/python -m engine.trial_dashboard --all

# Run tests
.venv/bin/python -m pytest tests/ -v

# Check vulcan paper state
vulcan paper status -o json
```

## Key Rules

- **Paper trades execute on Phoenix Perps via Vulcan with real prices.** No historical replay or synthetic fills.
- **No live trading** without explicit human approval.
- **No signing, no fund movement, no paid API spend** without explicit human approval.
- Signal weights are FIXED -- never adjusted by outcomes.
- Position sizing is never reduced due to prior losses.
- Cron installation requires explicit human approval.

## Requirements

- Python 3.13+
- [Vulcan CLI](https://github.com/Ellipsis-Labs/vulcan-cli) (for Phoenix paper trading)
- [Droid](https://factory.ai) (for AI account -- `droid exec` with GLM-5.1)
- [twitter-cli](https://github.com/public-clis/twitter-cli) (for AI agent CT intel -- optional, degrades gracefully)
- Solana RPC access (public mainnet RPC by default)

## What's Not Yet Done

- **Catalyst signal (deterministic)** -- Kukapay returns `unknown` when unreachable (5% weight, degrades gracefully)
- **Whale intelligence in production** -- Dextrabot JSON API needs live verification
- **Promotion gates** -- 0/8 passed; requires accumulated paper-trade history
- **Live trading** -- blocked on promotion gates and explicit human approval
- **Liquidation cluster signal** -- Deferred; MoonDev Data Layer is no longer available as a data source for liquidation events
