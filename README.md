# Imperial Agent

Live-paper crypto perps research and trading agent for Solana perpetual futures (via Imperial API) with Hyperliquid cross-venue reference data.

The full mission spec is in `MISSION.md`.

## Current Status

**Mode: `live-paper-only`** -- no live trading, no signing, no fund movement.

All milestones are complete. **414 tests pass.** The agent runs a full 14-step scan loop with real signal extraction, ATR-based stops, playbook-driven trade construction, forward outcome evaluation, and weekly review — all verified against the live Imperial API.

## What's Built

### Config (`config/`)
- `run.yaml` -- mode, schedule (08:15 / 17:15 / 23:15 AEST), account params (100 USDC, aggressive-paper)
- `venues.yaml` -- Imperial API base URL, 4 Solana perp venue codes, reference venues
- `risk.yaml` -- equity 100, max risk 20%, leverage 9-12x, cancel rules (90min timeout, 0.8 drift, 22:00 hard exit)
- `sources.yaml` -- 8 source tier definitions with confidence bases and fallback order

### Adapters (`adapters/`)
- `base.py` -- DataPoint, Provenance, SourceTier, AdapterHealth, DataAdapter protocol
- `normalizer.py` -- symbol normalization (BTC-PERP -> BTC), consistent provenance tagging
- `imperial.py` -- Imperial API client: mark prices, funding rates, stats/markets, OI history, route cost breakdown, status, depth data, candle history
- `flash_trade.py` -- Flash Trade MCP normalization layer (markets, prices, leverage, pool utilization)
- `phantom.py` -- Phantom MCP normalization layer (Hyperliquid markets, funding, OI, positions)
- `dextrabot.py` -- Web scraper with rate limiting, response caching, entity classification (smart_money / whale_unlabeled / unknown)

### Engine (`engine/`)
- `volatility.py` -- ATR computation (1h / 14-period), realized volatility, regime classification (Quiet/Normal/High/Extreme), ATR-based minimum stop distance (replaces 2% placeholder)
- `signals.py` -- Real signal extraction from live DataPoints: `funding_stretch` (rate vs 7d avg/stdev), `oi_delta` (24h/1h OI change % with direction), `basis` (Imperial vs Hyperliquid bp), `liquidity_magnet` (depth within 0.5%/1%/2%), `session_structure` (session VWAP, VAH/VAL/POC), `whale_evidence` (Dextrabot + wallet positions), `dex_perp_lag` (timestamp/price cross-venue), `volatility` (ATR percentile + regime), `catalyst` (placeholder with confidence 0)
- `playbooks.py` -- Playbook generation with 7 setup types: `breakout`, `fade`, `vwap_reclaim`, `lvn_rejection`, `liquidity_sweep`, `funding_fade`, `momentum_continuation`. Each playbook includes entry, stop, TP1/TP2, invalidation level, expected R:R, probability band, and rationale
- `scoring.py` -- GraphSignalScore with 9 weighted components (funding 15%, OI 15%, basis 10%, liquidity 15%, session 10%, whale 10%, DEX/lag 10%, volatility 10%, catalyst 5%), unknown handling, conflict logging
- `run_scan.py` -- Full 14-step scan loop with 3 modes: `plumbing-dry-run`, `live-paper`, `evaluate-outcomes`. Universe selection, data fetch (Imperial + Flash Trade + Phantom + Dextrabot), signal extraction, scoring, playbook selection, ATR-based position sizing, paper order creation, report generation, mission state persistence. `evaluate-outcomes` mode reads open orders, fetches mark prices, evaluates fills, computes outcomes (R/MAE/MFE with fee/slippage deduction), enforces cancel rules, writes signal attribution
- `report.py` -- Markdown report writer with all A-K sections populated with real data (sections C/D/E include evidence tables, signal breakdowns, and playbook details), AEST timestamps, run IDs
- `weekly_review.py` -- Weekly review: paper-trade expectancy, max drawdown, profit factor, fill/cancel/no-trade rates, per-signal stats, top 3 recommendations, next build pick. Returns `WeeklyReviewResult` dataclass
- `kg.py` -- Knowledge graph triple writer (SYMBOL, VENUE, WALLET, SIGNAL, etc.), CSV persistence, query support
- `paper_orders.py` -- Paper order model, maker-only fill logic, cancel rules (timeout/drift/hard exit), passive entry validation, conservative same-candle resolution
- `outcomes.py` -- Outcome evaluator (R, MAE, MFE, fees, slippage), signal-to-outcome attribution
- `risk.py` -- Position sizing (risk_usd / ATR-based stop_distance, leverage cap, lot rounding), passive entry gate, skipped trade logging
- `cross_venue.py` -- Basis comparison (bp), volume/OI dominance, whale signal integration (evidence-only, never copy-trade), conflict detection
- `hypothesis.py` -- Hypothesis registry CRUD (active/rejected/superseded)
- `source_health.py` -- Source health tracker (latency, freshness, confidence adjustment), signal outcome scorer

### Ledgers (`ledgers/`)
All 10 ledger files with correct schemas per MISSION.md:
- `paper_orders.csv`, `paper_fills.csv`, `outcomes.csv` -- order/fill/outcome tracking
- `evidence_ledger.jsonl` -- append-only evidence log
- `kg_triples.csv` -- knowledge graph triples
- `signal_outcomes.csv` -- per-signal hit rate and avg R
- `skipped_trades.csv` -- rejected candidates with reasons
- `source_health.csv`, `hypothesis_registry.csv`, `improvement_backlog.csv`

### Memory (`memory/`)
- `mission_state.json` -- mode, run ID, open orders, promotion status (0/8 gates passed)
- `durable_lessons.md`, `adapter_registry.md`, `failure_modes.md`, `promotion_decisions.md`

### Scripts (`scripts/`)
- `run_scan.sh` -- Cron entry point, accepts `--mode plumbing-dry-run`, `--mode live-paper`, or `--mode evaluate-outcomes`
- `evaluate_outcomes.sh` -- Outcome evaluation runner (delegates to `run_scan.sh --mode evaluate-outcomes`)
- `weekly_review.sh` -- Weekly review cron entry point

### Reports (`reports/`)
Timestamped markdown reports with sections A-K, generated by each scan run. Sections C (evidence), D (signal breakdown), and E (playbook details) are populated with real data.

### Tests (`tests/`)
414 tests across test files covering all validation contracts, integration tests, and cross-module pipelines.

## Quick Commands

```bash
# Plumbing dry run (no network calls, no paper orders)
./scripts/run_scan.sh --mode plumbing-dry-run

# Live paper scan (fetches data, may create paper orders)
./scripts/run_scan.sh --mode live-paper

# Evaluate open paper orders against current prices
./scripts/run_scan.sh --mode evaluate-outcomes

# Weekly review of paper-trade performance
./scripts/weekly_review.sh

# Run tests
.venv/bin/python -m pytest tests/ -v

# Compile check
.venv/bin/python -m py_compile engine/run_scan.py
```

## Key Rules

- **Paper results must come from live forward paper trades only.** Historical replay, synthetic fills, and backtests cannot count as paper-trading outcomes or promotion evidence.
- **No live trading** without explicit human approval and all 8 promotion gates passed.
- **No signing, no fund movement, no paid API spend** without explicit human approval.
- Mode is read from `memory/mission_state.json` at the start of every run.

## What's Not Yet Done

These areas need real-world exercising and refinement:

- **Catalyst scan** -- no macro/token unlock/maintenance calendar integration (currently returns `unknown` with confidence 0; 5% weight)
- **Whale intelligence in production** -- Dextrabot scraping works but needs live HTML verification for entity classification accuracy
- **Cron installation** -- not auto-installed (requires human approval)
- **Promotion gates** -- 0/8 passed; requires accumulated paper-trade history before live promotion can be considered
- **Live trading** -- blocked on promotion gates and explicit human approval

