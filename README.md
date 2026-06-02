# Imperial Agent

Live-paper crypto perps research and trading agent for Solana perpetual futures (via Imperial API) with Hyperliquid cross-venue reference data. Runs two parallel paper accounts -- deterministic and AI -- for A/B comparison.

The full mission spec is in `MISSION.md`.

## Current Status

**Mode: `live-paper-only`** -- no live trading, no signing, no fund movement.

**581 tests pass.** A cron job fires every hour on the hour, running two independent paper trading accounts in parallel:

1. **Deterministic account** (`accounts/deterministic/`) -- 14-step scan loop with fixed signal weights, 9 signal extractors, 7 playbook types. Active and producing trades.
2. **AI account** (`accounts/ai/`) -- AI reasoning via repo-local skills + agent delegation. Each cycle: builds an enriched prompt from live market data + active skills, writes `ai_prompt.txt` and `ai_request.json`, invokes the bridge command (`scripts/ai_delegate_agent.sh`), then reads `ai_response.json` if a prompt-bound response is present. Falls closed to no-trade when no agent response is available.

## Account Structure

Each account has fully isolated state under `accounts/<id>/`:

```
accounts/
  deterministic/           # Signal-scoring agent
    ledgers/               # paper_orders.csv, outcomes.csv, signal_outcomes.csv, ...
    reports/               # timestamped markdown reports
    memory/                # mission_state.json
    data/                  # auto-generated prompt/log files

  ai/                      # AI reasoning agent
    ledgers/               # (same schema)
    reports/
    memory/
    data/                  # ai_prompt.txt, ai_request.json (output), ai_response.json (agent input)
```

Compare both accounts: `python -m engine.trial_dashboard --all`

## What's Built

### Config (`config/`)
- `run.yaml` -- mode, hourly schedule, 1000 USDC equity, max 4 concurrent, max 3 candidates
- `risk.yaml` -- equity 1000, max risk 20%, leverage 9-12x, 45-min cancel timeout, no hard exit
- `ai_agent.yaml` -- MCP source config, response provider (agent_file delegation), skills config, bridge command, fallback behavior
- `venues.yaml` -- Imperial API base URL, 4 Solana perp venue codes, reference venues
- `sources.yaml` -- 8 source tier definitions with confidence bases and fallback order

### Adapters (`adapters/`)
- `base.py` -- DataPoint, Provenance, SourceTier, AdapterHealth, DataAdapter protocol
- `normalizer.py` -- symbol normalization (BTC-PERP -> BTC), consistent provenance tagging
- `imperial.py` -- Imperial API client: mark prices, funding rates, stats/markets, OI history, route cost, depth data
- `flash_trade.py` -- Flash Trade MCP normalization layer
- `phantom.py` -- Phantom MCP normalization layer (Hyperliquid markets, funding, OI, positions)
- `dextrabot.py` -- Web scraper with rate limiting, entity classification

### Engine -- Deterministic Pipeline (`engine/`)
- `signals.py` -- 9 signal extractors from live DataPoints (funding_stretch, oi_delta, basis, liquidity_magnet, session_structure, whale_evidence, dex_perp_lag, volatility, catalyst)
- `scoring.py` -- Fixed weighted scoring (9 components, weights sum to 1.0, never adjusted by outcomes)
- `playbooks.py` -- 7 setup types (breakout, fade, vwap_reclaim, funding_fade, momentum_continuation, liquidity_sweep, lvn_rejection)
- `risk.py` -- Position sizing (risk_usd / ATR stop_distance, leverage 9-12x cap, lot rounding, passive entry gate)
- `paper_orders.py` -- Paper order model, maker-only fill logic, cancel rules, passive entry validation
- `outcomes.py` -- Outcome evaluator (R, MAE, MFE, fees, slippage), signal attribution
- `volatility.py` -- ATR computation, regime classification, minimum stop distance
- `cross_venue.py` -- Basis comparison, whale signal integration, conflict detection
- `report.py` -- Markdown report writer (sections A-L with live data)
- `weekly_review.py` -- Weekly review with expectancy, drawdown, profit factor, recommendations
- `kg.py`, `hypothesis.py`, `source_health.py` -- Knowledge graph, hypothesis tracking, source health

### Engine -- AI Pipeline (`engine/`)
- `mcp_data.py` -- Parses MCP tool output (Flash Trade trading overview, Phantom perps account/positions/markets) into DataPoints. Builds the enriched AI prompt from live market data + active skills (unified table with price, funding, OI, volume, leverage, pool utilization, ATR estimates). Requires prompt-bound JSON response matching a prompt_id.
- `ai_agent.py` -- Parses AI JSON responses into trade candidates, validates prompt_id binding, validates candidates (stop side, R:R >= 2, ATR floor, evidence/risk_notes/data_gaps), generates report sections
- `skills.py` -- Loads repo-local trading skills from `skills/<name>/SKILL.md`, truncates to configured max chars, injects into AI prompt at runtime
- `run_scan.py` -- 4 modes: `plumbing-dry-run`, `live-paper` (deterministic), `evaluate-outcomes`, `ai-paper`. Each mode accepts `--account <id>` for account isolation. AI mode: builds prompt + skills, writes request metadata, invokes bridge command, reads prompt-bound response, validates, risk-sizes. Auto-evaluates open orders from previous cycle before placing new ones

### AI Trading Skills (`skills/`)
Repo-local prompt modules that enrich the AI agent's reasoning. Controlled by `config/ai_agent.yaml` under `skills.enabled`. Each skill adds a specific data lens, analysis pattern, or reasoning constraint. New skills can be added as `skills/<name>/SKILL.md` and listed in config.

- `core-trader-mandate` -- Elite perps trader posture: hunt asymmetric setups, reject weak trades, urgency + selectivity, no invented data
- `hyperliquid-microstructure` -- Funding stretch, OI expansion/contraction, volume concentration, passive entry feasibility, setup pattern recognition (funding_fade, momentum_continuation, liquidity_sweep, fade)
- `solana-perps-context` -- Solana-native venue data (Flash Trade, Jupiter, Phoenix), pool utilization, SOL core symbol beta behavior
- `whale-leaderboard-intel` -- Whale/leaderboard classification (smart_money, whale_unlabeled, unknown), no copy-trade rule, multi-source overlap preference
- `risk-execution-rails` -- Hard constraints: equity, risk %, leverage range, passive entry, stop/TP math, R:R minimums, duplicate position prevention
- `provenance-auditor` -- Evidence provenance: 2-5 evidence tags per trade, risk_notes for invalidation, data_gaps for missing evidence, no fabricated source claims
- `outcome-learning` -- Informational prior outcomes: notice weak patterns, explain confidence changes, never adjust weights or reduce sizing

### Scripts (`scripts/`)
- `cron_hourly.sh` -- Cron entry point. Runs both accounts every hour: deterministic scan, then AI scan (builds prompt, invokes bridge, reads response). AI fails closed to no-trade if no agent response
- `run_scan.sh` -- CLI wrapper, accepts `--mode` and `--account`
- `ai_delegate_agent.sh` -- Bridge stub invoked by AI mode after writing prompt/request files. Default: safe no-op. Wire to Hermes or Droid when agent API contract is available
- `trial_start.sh` -- Backs up config, applies trial config, verifies with dry-run, prints cron line
- `trial_stop.sh` -- Removes cron, runs final evaluation, restores config, prints trial summary
- `evaluate_outcomes.sh`, `weekly_review.sh` -- Evaluation and review runners

### Trial Dashboard (`engine/trial_dashboard.py`)
CLI dashboard showing scan count, order counts, win/loss/expectancy R, per-signal hit rates, per-setup stats, trial elapsed/remaining. Supports `--account <id>` and `--all` for side-by-side comparison.

### Memory (`memory/`)
- `mission_state.json` -- mode, run ID, promotion status (0/8 gates)
- `durable_lessons.md`, `adapter_registry.md`, `failure_modes.md`, `promotion_decisions.md`

### Tests (`tests/`)
581 tests covering all validation contracts, engine modules, cross-module pipelines, account isolation, AI parsing, MCP data normalization, skills loading, prompt-bound response validation, and end-to-end trial cycles.

## Quick Commands

```bash
# Run deterministic paper scan
./scripts/run_scan.sh --mode live-paper --account deterministic

# Run AI paper scan (builds prompt, invokes bridge, reads response)
./scripts/run_scan.sh --mode ai-paper --account ai

# AI mode writes these files each cycle:
#   accounts/ai/data/ai_prompt.txt    — enriched prompt with market data + skills
#   accounts/ai/data/ai_request.json  — prompt_id, metadata, expected response shape
#   accounts/ai/data/ai_response.json — agent response (written by bridge/agent)

# Plumbing dry run (no network calls)
./scripts/run_scan.sh --mode plumbing-dry-run

# Evaluate open orders
./scripts/run_scan.sh --mode evaluate-outcomes --account deterministic

# Trial dashboard (both accounts)
.venv/bin/python -m engine.trial_dashboard --all

# Single account dashboard
.venv/bin/python -m engine.trial_dashboard --account ai

# Weekly review
./scripts/weekly_review.sh

# Run tests
.venv/bin/python -m pytest tests/ -v

# Start 24-hour trial (prints cron line for human approval)
./scripts/trial_start.sh

# Stop trial and print final summary
./scripts/trial_stop.sh
```

## Files Each Agent Reads

### Deterministic agent (every cycle)
1. `config/run.yaml` -- equity, max candidates, schedule
2. `config/risk.yaml` -- leverage bounds, cancel timeout, risk %
3. `accounts/deterministic/memory/mission_state.json` -- mode gate + open orders
4. `accounts/deterministic/ledgers/signal_outcomes.csv` -- prior signal hit rates (display only)
5. Imperial API -- mark prices, funding rates, volume, OI, depth (live network)
6. `engine/scoring.py` -- fixed signal weights
7. `engine/signals.py` -- 9 signal extractors
8. `engine/playbooks.py` -- 7 setup types
9. `engine/risk.py` -- position sizing math

### AI agent (every cycle)
1. `config/run.yaml` -- equity, max candidates
2. `config/risk.yaml` -- leverage bounds, risk %, cancel timeout
3. `config/ai_agent.yaml` -- MCP source config, response provider, skills config, bridge command
4. `accounts/ai/memory/mission_state.json` -- mode gate + open orders + MCP data blobs
5. `accounts/ai/ledgers/signal_outcomes.csv` -- prior signal stats (informational only)
6. `skills/<enabled-skills>/SKILL.md` -- active trading skills injected into prompt
7. Imperial API -- mark prices, funding rates, volume, OI, depth (live network, MCP fallback)
8. `engine/risk.py` -- position sizing math (same as deterministic)
9. `accounts/ai/data/ai_response.json` -- **agent-delegated trade decisions** (written by Droid/Hermes bridge, must match prompt_id)

## Key Rules

- **Paper results must come from live forward paper trades only.** No historical replay, synthetic fills, or backtests.
- **No live trading** without explicit human approval and all 8 promotion gates passed.
- **No signing, no fund movement, no paid API spend** without explicit human approval.
- Signal weights are FIXED -- never adjusted by outcomes.
- Position sizing is never reduced due to prior losses.
- Cron installation requires explicit human approval.

## What's Not Yet Done

- **Agent bridge wiring** -- `scripts/ai_delegate_agent.sh` is a safe no-op stub. To produce AI trades, wire it to Hermes or Droid so it reads the prompt and writes a prompt-bound JSON response. The bridge receives paths via `IMPERIAL_AI_PROMPT_PATH`, `IMPERIAL_AI_RESPONSE_PATH`, `IMPERIAL_AI_PROMPT_ID` environment variables.
- **Catalyst signal** -- returns `unknown` with confidence 0 (5% weight)
- **Whale intelligence in production** -- Dextrabot scraping needs live HTML verification
- **Promotion gates** -- 0/8 passed; requires accumulated paper-trade history
- **Live trading** -- blocked on promotion gates and explicit human approval

