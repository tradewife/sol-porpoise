# Market Structure Context

This skill evaluates higher-timeframe (HTF) regime, key levels, session, and volume
profile context BEFORE any microstructure or breakout signal is considered.
It classifies context for each market as `structure_confirmed`, `structure_partial`,
or `structure_rejected`. It does not place trades or size positions; it only tags
context for other skills and the AI agent to reference.

## What this skill is for

This skill tells the AI how to read the structured market-data sections that
`mcp_data.py format_ai_prompt()` already injects into every hourly prompt.
It is NOT a new data source. It is a reasoning lens that sits over data the
engine already collects and delivers in the Market Data table.

The hourly cron (--mode ai-paper) assembles the prompt automatically.
This skill fires at reasoning time, not at data-collection time.

## Prompt sections this skill interprets

| Prompt section | Source | What it contains |
|---|---|---|
| Market Data table | mcp_data.py | Price, funding rate, OI, 24h volume, max leverage, pool utilisation |
| ATR Estimates | mcp_data.py | 1.5%-of-price ATR proxy per symbol |
| Account | mcp_data.py | Paper equity, available USDC, max concurrent trades |
| Existing Positions | run_scan.py Phantom call | Open positions with entry, size, unrealised PnL |
| Prior Signal Performance | run_scan.py | Per-signal hit rate, avg R -- informational only |
| Hawk Breakout Signals | run_scan.py (when present) | Pre-computed breakout signal per symbol from engine/hawk_breakout.py |

Do not fabricate any of these values. If a cell shows -, treat that field as unavailable.

## HTF regime classification

Classify each symbol into one regime using the Market Data table:

| Regime | Signals | Candidate bias |
|---|---|---|
| trending_up | Rising OI + price, moderate-positive funding, rising volume | Momentum continuation longs; hawk breakouts above 7d high valid |
| trending_down | Falling price + rising OI (shorts entering), negative or flipping funding | Momentum continuation shorts; hawk breakouts below 7d low valid |
| ranging | Flat or falling OI, flat volume, near-zero funding | Raise evidence bar; fade setups preferred over breakouts |
| compression | Very flat price, collapsing volume, near-zero OI change | Avoid breakout entries; wait for expansion |
| unknown | Key columns show - | Treat as structure_rejected |

## Reading the Market Data table

### Funding rate
- Positive funding (longs pay): stretched positive (above ~0.01 per 8h proxy) means short-lean.
- Negative funding: favours longs.
- Near-zero: neutral.
- Classify: funding_long_lean, funding_short_lean, funding_neutral.

### Open interest
- Rising OI + rising price = new longs entering, confirms upside momentum.
- Rising OI + falling price = new shorts entering, confirms downside momentum.
- Falling OI = position closure, weakens trend conviction.
- Missing (-): flag as unavailable; do not assume direction.

### 24h volume
- High volume relative to OI confirms conviction.
- Low volume on a big price move = suspect; likely fade candidate.
- Volume missing: note in data_gaps.

### Pool utilisation (Flash Trade / Solana venues)
- Above 75%: note in risk_notes; fills on large notional less reliable.
- Below 50%: normal liquidity, no haircut.

### Max leverage
- Below 9x: market cannot satisfy the 9-12x leverage constraint; exclude from candidates.

## ATR and stop sizing

Stop distance must be >= 0.8 x ATR from entry. The ATR Estimates section gives
the floor. Accept the 1.5%-of-price proxy when no candle history is available.
If the only viable setup requires a tighter stop, return no trade.

## Alignment scoring

Before generating candidates, score each symbol 0-4:

| Check | Points |
|---|---|
| HTF regime is trending_up or trending_down (not ranging/compression) | +1 |
| OI direction confirms price direction | +1 |
| Funding direction aligns with candidate direction | +1 |
| Volume is present and at least neutral | +1 |

| Score | Classification |
|---|---|
| 4 | structure_confirmed |
| 2-3 | structure_partial |
| 0-1 | structure_rejected |

## Hawk Breakout Signals section

When the prompt contains a Hawk Breakout Signals section, read it as follows:

- Each entry is a pre-computed result from engine/hawk_breakout.py.
- Fields: market, signal (long/short/none), score (0-9), basis.
- signal = "none" or missing: no breakout candidate; skip that symbol for
  breakout and momentum_continuation setups.
- score >= 7 with structure_confirmed: strong candidate; treat as high probability.
- score 5-6 with structure_partial: medium probability.
- score < 5 or structure_rejected: do not enter a breakout trade.
- The hawk score does NOT replace the full evidence checklist. Still populate
  evidence, risk_notes, and data_gaps from all available prompt data.

## Setup type selection

Map regime + hawk signal to setup type:

- breakout: hawk signal present, structure_confirmed, OI expanding, volume confirming.
- momentum_continuation: trending regime, OI + volume expanding, no breakout gate needed.
- fade: stretched move, OI not confirming, weak or exhausted volume.
- funding_fade: extreme funding with price structure not confirming direction.
- vwap_reclaim: overextension from VWAP with mean-reversion evidence.
- liquidity_sweep: price near stop cluster, reversal evidence present.
- custom: only when none of the above applies; rationale must be explicit.

## Evidence tagging rules

Every candidate must include 2-5 evidence tags in the evidence array.
Tags must come from prompt sections; never tag unavailable data.

- funding_long_lean:<symbol> / funding_short_lean:<symbol>
- oi_expanding_long:<symbol> / oi_expanding_short:<symbol> / oi_contracting:<symbol>
- volume_confirming:<symbol> / volume_weak:<symbol>
- pool_util_high:<symbol>: when pool utilisation > 75%
- hawk_breakout_confirmed:<symbol>: when hawk signal matches direction and score >= 5
- hawk_sm_tilt:<symbol>: when basis.sm_tilt_supports = true in hawk signal
- atr_stop_valid:<symbol>: confirms stop distance >= 0.8 ATR
- CT-sentiment:<symbol>:<direction>: only when twitter-ct-intel section present and corroborated

## Interaction with other skills

| Skill | Relationship |
|---|---|
| core-trader-mandate | Overarching identity; always wins on conflicts |
| risk-execution-rails | Hard numeric constraints; this skill must propose numbers that pass those rails |
| hawk-breakout | This skill reads hawk output; hawk is the signal; market-structure-context is the gate |
| hyperliquid-microstructure | Same Market Data table, HL-specific patterns; use both lenses |
| solana-perps-context | Pool utilisation, venue constraints; defer to it on venue liquidity reads |
| whale-leaderboard-intel | Corroborating signal; never primary thesis |
| twitter-ct-intel | Soft context; lowest weight; never overrides structural read |
| provenance-auditor | Every evidence tag must map to an actual prompt section |
| outcome-learning | Prior stats are informational; do not suppress a strong current setup |

## What this skill does NOT do

- Does not add a new data fetch or change the cron schedule.
- Does not call any adapter, write any file, or modify mission state.
- Does not interact with engine/signals.py, engine/scoring.py,
  engine/playbooks.py, or the --mode live-paper deterministic path.
- Does not override risk-execution-rails numeric checks.
- Does not add a Market Structure Context section to the prompt -- the
  Market Data table already contains this data.
