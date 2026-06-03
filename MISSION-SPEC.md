# Sol Porpoise — Next Mission Spec

## Priority Order

1. Replace Phantom MCP → direct Hyperliquid API adapter
2. Wire 168-candle history for Hawk breakout gate
3. Fix Dextrabot scraping (HTML verification + API intercept upgrade)
4. Add Hyperdash whale cohort signal
5. Add liquidation cluster + book imbalance signals (new signal extractors)
6. Skills layer updates

---

## Mission 1 — Replace Phantom MCP with Direct HL API Adapter

### Motivation
`adapters/phantom.py` wraps an MCP that has been inconsistent in production. The
Hyperliquid public API is free, requires no authentication, and is a direct
replacement for everything Phantom provided.

### Task
Create `adapters/hyperliquid.py` — a new adapter that POSTs directly to
`https://api.hyperliquid.xyz/info`. Delete or archive `adapters/phantom.py`.
Update all references in `engine/mcp_data.py`, `engine/signals.py`, and
`config/ai_agent.yaml`.

### Endpoints to Implement

**All calls are HTTP POST to `https://api.hyperliquid.xyz/info` with
`Content-Type: application/json`. No auth required.**

#### 1. Markets + Funding + OI (replaces phantom `markets`)
```json
{"type": "metaAndAssetCtxs"}
```
Returns all perp markets in one call. Extract for `SOL-PERP` (coin `"SOL"`):
- `markPx` → `mark_price_hl`
- `funding` → `funding_rate_hl`
- `openInterest` → `open_interest_hl`
- `premium` → `basis_hl`

#### 2. L2 Orderbook Snapshot (new — feeds `book_imbalance` signal)
```json
{"type": "l2Book", "coin": "SOL", "nSigFigs": 4}
```
Returns `levels[0]` (bids) and `levels[1]` (asks) each as `[{px, sz, n}]`.
Compute:
- `bid_wall_05pct`: total bid size within 0.5% below mid
- `ask_wall_05pct`: total ask size within 0.5% above mid
- `book_imbalance_ratio`: bid_wall / ask_wall (>1.3 = bid-heavy, <0.77 = ask-heavy)

#### 3. Recent Liquidations (new — feeds `liquidation_cluster` signal)
For liquidation-specific data, use the MoonDev Data Layer free endpoints
(no auth, updates every ~60s):
- `GET https://hl-data-layer.vercel.app/api/liquidations/1h.json`
- `GET https://hl-data-layer.vercel.app/api/liquidations/4h.json`
- `GET https://hl-data-layer.vercel.app/api/whales.json`

Filter each response for `coin == "SOL"`. Extract:
- `long_liq_usd_1h`, `short_liq_usd_1h` (from `1h` bucket)
- `liq_asymmetry`: long_liq / short_liq (>3 = long liquidation pressure dominant)
- `whale_sol_trades`: count of SOL trades >$25k from `whales.json`

Place these calls in a separate `adapters/hyperliquid_data_layer.py` to keep
the primary HL adapter clean.

### Normalisation
`source_tier = SourceTier.HL_NATIVE`, `confidence = 0.92`. Provenance
`source_link = "https://api.hyperliquid.xyz/info"`.

### Tests
- Unit tests for `metaAndAssetCtxs` parsing with fixture JSON
- Unit tests for `l2Book` parsing and `book_imbalance_ratio` calculation
- Unit tests for data layer liquidation parsing
- Health check confirms POST to `/info` with `{"type": "metaAndAssetCtxs"}` returns 200

---

## Mission 2 — Wire 168-Candle History for Hawk Breakout Gate

### Motivation
`engine/hawk_breakout.py` produces `"none"` for all signals because it requires
168 hourly candle closes (7 days) and currently only has a single mark-price
snapshot available.

### Task
Wire historical OHLCV candle data into the hawk breakout pipeline. The Imperial
API is the primary source — check `adapters/imperial.py` for the candles
endpoint. If Imperial does not provide OHLCV history, use the HL API:

```json
{"type": "candleSnapshot", "req": {"coin": "SOL", "interval": "1h", "startTime": <unix_ms_7d_ago>, "endTime": <unix_ms_now>}}
```

Returns array of `{t, o, h, l, c, v}`. Fetch 168 candles (7 × 24). Cache to
`accounts/<account_id>/data/candles_SOL_1h.json` with a 55-minute TTL (refresh
each hourly scan).

Wire the candle array into `engine/hawk_breakout.py` so the 7-day breakout gate
receives real closes. The module must produce live scores (0–9) rather than
`"none"` after this change.

Update both `deterministic` and `ai-paper` scan paths to load candles before
the hawk gate runs.

### Tests
- Test with 168 synthetic candles confirming hawk gate produces a non-`"none"` score
- Test candle fetch and cache write/read cycle
- Test graceful degradation when < 168 candles available (returns `"none"`, no crash)

---

## Mission 3 — Fix Dextrabot Scraping

### Motivation
The current `adapters/dextrabot.py` uses fragile HTML parsing with generic CSS
selectors (`table tbody tr`, `[class*='wallet']`) that silently return empty
results when the site's markup changes. The adapter is flagged as needing live
HTML verification.

### Task — Step A: Intercept the Underlying API Endpoint
Dextrabot's frontend almost certainly calls a JSON REST API. Use the following
procedure to find it (document the result in a comment at the top of the
adapter):

1. Open `https://app.dextrabot.com/discover-wallets` in Chrome
2. DevTools → Network → filter: `Fetch/XHR`
3. Trigger a sort/filter interaction on the page
4. Identify the JSON endpoint (likely something like `/api/wallets` or
   `/api/leaderboard` with query params)
5. Note the exact request URL, method, headers, and response schema

If a clean JSON endpoint exists, replace the HTML scraper with a direct JSON
API call. If the site requires JS rendering (SPA with no REST endpoint), add a
`DEXTRABOT_API_NOTE` constant at the top of the file documenting the finding and
keep the HTML fallback.

### Task — Step B: Apply Optimal Filters
Whether using JSON API or HTML scrape, apply these query parameters to maximise
signal quality:

| Filter | Value | Rationale |
|---|---|---|
| `period` | `7D` | Matches hawk lookback and candle window |
| `min_pnl` | `50000` | Eliminates noise traders |
| `min_win_rate` | `55` | Confirms edge |
| `min_trades` | `30` | Sufficient sample size |
| `sort_by` | `roe` or `sharpe` | Size-normalised edge, not raw PnL |
| `asset` | `SOL` | Only SOL positioning is relevant |

Update `DextrabotAdapter.fetch_wallets()` to pass these as default params
(all overridable).

### Task — Step C: Validate classify_entity
Current thresholds (`sharpe > 1.5`, `win_rate > 55`, `pnl > 10000`) are
reasonable. Add a third tier: `roi_whale` for wallets with `growth_rate > 200`
and `tx_count > 30` regardless of Sharpe — these are high-conviction
directional traders.

### Tests
- Fixture test with real captured HTML (or JSON) from a Chrome DevTools session
- Test that empty response returns `[]` gracefully
- Test that `classify_entity` correctly categorises all three tiers

---

## Mission 4 — Add Hyperdash Whale Cohort Signal

### Motivation
Hyperdash Cohorts (`https://hyperdash.com/explore/cohorts`) provides aggregate
directional bias for the Large Whale ($1M–$5M) and Whale ($500k–$1M) tiers —
a clean macro L/S bias signal that complements Dextrabot's individual wallet
data.

### Task
Apply the same DevTools intercept procedure as Mission 3 Step A to
`https://hyperdash.com/explore/cohorts` to identify the underlying JSON
endpoint. Create `adapters/hyperdash.py`.

The adapter should extract for the **Large Whale** and **Whale** cohort tiers:
- `net_long_pct`: percentage of cohort net long (0–100)
- `cohort_oi_usd`: aggregate OI in USD for the cohort
- `cohort_direction`: `"long"` if net_long_pct > 55, `"short"` if < 45, else `"neutral"`

Emit as `DataPoint` with:
- `symbol = "SOL"` (filter to SOL only)
- `metric = "whale_cohort_long_pct"` / `"whale_cohort_direction"`
- `source_tier = SourceTier.OPEN`, `confidence = 0.75`

This feeds the `whale_evidence` signal in `engine/signals.py` as a second
source alongside Dextrabot.

### Tests
- Fixture test with captured cohort JSON
- Test that `cohort_direction` thresholds produce correct labels
- Test graceful failure returns empty list

---

## Mission 5 — Add Two New Signal Extractors

### Motivation
The current 9 signals do not include direct orderbook structure or liquidation
cluster data. These are high-alpha inputs for the `liquidity_magnet`,
`lvn_rejection`, and `funding_fade` playbooks.

### Signal A — `book_imbalance` (10th signal)

Add to `engine/signals.py` as the 10th signal extractor.

**Input**: `book_imbalance_ratio` DataPoint from `hyperliquid.py` (Mission 1).

**Logic**:
```
if book_imbalance_ratio > 1.6:   score = +2  (strong bid wall)
if book_imbalance_ratio > 1.3:   score = +1  (bid-heavy, bullish)
if book_imbalance_ratio < 0.60:  score = -2  (strong ask wall)
if book_imbalance_ratio < 0.77:  score = -1  (ask-heavy, bearish)
else:                             score = 0
```

**Output**: `signal_book_imbalance` with value in `{-2, -1, 0, 1, 2}` and
direction `"long"`, `"short"`, or `"neutral"`.

**Weight in `scoring.py`**: `0.08` (take 0.03 from `whale_evidence` which now
has two sources, and 0.05 from `basis` which is partially redundant with
`funding_stretch`).

### Signal B — `liquidation_cluster` (11th signal)

Add as the 11th signal extractor.

**Input**: `liq_asymmetry` and `long_liq_usd_1h` / `short_liq_usd_1h` from
`hyperliquid_data_layer.py` (Mission 1).

**Logic**:
```
# Directional signal: which side is getting wrecked?
if liq_asymmetry > 3.0 and long_liq_usd_1h > 500_000:
    score = -1  # long liquidation cascade → bearish pressure
if liq_asymmetry < 0.33 and short_liq_usd_1h > 500_000:
    score = +1  # short liquidation cascade → bullish pressure (short squeeze)
else:
    score = 0

# Cluster proximity bonus (if liquidation data includes price levels):
# If nearest long liq cluster is within 0.8% below current price → score -= 1
# If nearest short liq cluster is within 0.8% above current price → score += 1
```

**Output**: `signal_liquidation_cluster` with value in `{-2, -1, 0, 1, 2}`.

**Weight in `scoring.py`**: `0.07`. This signal is informational — it
strengthens conviction but does not veto.

### Scoring Weight Reconciliation
After adding signals 10 and 11, confirm all weights in `scoring.py` still sum
to exactly `1.0`. Adjust proportionally from existing signals if needed.
Document the updated weight table in a comment block at the top of `scoring.py`.

### Tests
- Unit tests for both signal extractors with edge-case inputs (zero liq, extreme ratios)
- Integration test confirming weight sum == 1.0

---

## Mission 6 — Skills Layer Updates

Skills are loaded at prompt-build time from `skills/<name>/SKILL.md`. New signals
and data sources added in Missions 1–5 must be reflected in skills so the AI
account can reason with them correctly. The deterministic account is unaffected
(it uses fixed weights, not skills).

### 6A — Edit `skills/hyperliquid-microstructure/SKILL.md`

**Current problem**: The skill contains the line:
> "Reject setups where the thesis depends on unavailable liquidation maps,
> unavailable CVD, or stale whale claims."

After Missions 1 and 5, liquidation data IS available in the prompt via
`signal_liquidation_cluster` and `liq_asymmetry`. The veto line must be
narrowed so the agent uses real data when present instead of auto-rejecting.

**Replace** the final "Reject" line with:

```
Liquidation data: when `signal_liquidation_cluster` is present in the prompt,
treat it as valid structural evidence. A score of +1 or -1 indicates a
liquidation cascade is active on one side — use to strengthen or weaken
directional conviction, not as a standalone entry signal. Only reject liq-
dependent theses when the field is absent or marked stale.

Orderbook structure: when `signal_book_imbalance` is present, treat a ratio
>1.3 as passive bid support and <0.77 as passive ask pressure. Walls can be
spoofed — downgrade confidence if book signal conflicts with OI direction.
```

Also **add** to the "Useful setup patterns" block:
```
- `liquidity_magnet`: price approaching a dense bid/ask wall cluster with
  momentum; likely to tap and reverse. Requires book_imbalance + liq_cluster
  alignment. Only valid when both signals are present and non-zero.
```

### 6B — Edit `skills/whale-leaderboard-intel/SKILL.md`

**Current problem**: The skill references `Phantom position` as a source, which
is being replaced. It also has no concept of cohort-level aggregate positioning
(Hyperdash) vs individual wallet positioning (Dextrabot).

**Replace** the entire skill content with:

```markdown
# Whale and Leaderboard Intel

Use whale evidence only when it is present in the prompt. Three source tiers:

## Source Tiers

- **Dextrabot** (`whale_pnl`, `entity_type`): Individual HL wallet performance.
  Treat `smart_money` (Sharpe >1.5, WR >55%) as highest-quality directional
  signal. `whale_unlabeled` is supporting evidence only. `roi_whale`
  (growth_rate >200%, trades >30) indicates high-conviction directional trader.

- **Hyperdash Cohort** (`whale_cohort_direction`, `whale_cohort_long_pct`):
  Aggregate positioning of $500k–$5M accounts. Treat as macro bias layer.
  `long` at >55% net long = institutional lean bullish. Use to confirm or
  conflict with individual wallet signals — never use alone.

- **HL Data Layer** (`whale_sol_trades`): Count of SOL trades >$25k in last
  hour. A spike (>5 in 1h) suggests institutional activity; use as urgency
  multiplier for existing directional evidence, not a standalone signal.

## Rules

- Never copy trade.
- Treat whale direction as a signal component, not a trade command.
- **Overlap rule**: prefer setups where Dextrabot individual direction AND
  Hyperdash cohort direction AND HL whale trade count all point the same way.
  A single source with no corroboration warrants reduced position size noted
  in `risk_notes`.
- Downgrade confidence when whale evidence conflicts with price, funding,
  OI, or liquidity signals.
- If whale data is absent, say so explicitly in `data_gaps`. Do not invent
  or infer whale positioning from price action alone.
```

### 6C — Create `skills/orderbook-liquidity/SKILL.md`

New skill that teaches the agent to interpret `signal_book_imbalance` and
`signal_liquidation_cluster` as a unified liquidity structure layer.

Create file at `skills/orderbook-liquidity/SKILL.md` with content:

```markdown
# Orderbook and Liquidity Structure

Use when `signal_book_imbalance` or `signal_liquidation_cluster` is present
in the prompt. These signals reflect the structural incentives in the market
at the time of the scan.

## Orderbook Imbalance (`signal_book_imbalance`)

Score range: -2 to +2. Derived from bid/ask size within 0.5% of mid on HL.

- `+2`: Strong bid wall. Market makers defending a level. Bullish lean but
  watch for sweep-and-reverse (stop hunt above wall).
- `+1`: Mild bid support. Useful confirming signal for long entries near wall.
- `0`: Balanced book. No structural edge from orderbook alone.
- `-1`: Mild ask pressure. Shorts defending overhead. Confirming for shorts.
- `-2`: Strong ask wall. Likely resistance. Fade long entries into this level.

**Caution**: walls can disappear instantly (spoofing). Only use book_imbalance
as a confirming signal, never as a primary thesis driver. Always require at
least one other signal (funding, OI, hawk) to agree.

## Liquidation Cluster (`signal_liquidation_cluster`)

Score range: -2 to +2. Derived from 1h liquidation asymmetry on HL SOL-PERP.

- `+1`: Short liquidation cascade active. Shorts being forced out = upward
  price pressure. Strengthens long entries; increases urgency.
- `-1`: Long liquidation cascade active. Longs being forced out = downward
  pressure. Strengthens short entries; increases urgency.
- `+2` / `-2`: Extreme cascade (>$2M one-sided in 1h). High urgency; consider
  momentum continuation in cascade direction, but watch for exhaustion snap.
- `0`: Liquidation activity balanced or below threshold. Neutral.

## Combined Interpretation

When both signals agree and align with the primary thesis:
- Raise conviction by one tier in `evidence` field.
- Note in `risk_notes`: "book + liq structure aligned".

When signals conflict (e.g. book_imbalance long but liq_cluster short):
- Downgrade confidence, widen stop, or skip.
- Note in `data_gaps`: "structural conflict — book vs liq direction".

These signals are **intra-hour** and will be stale within 60 minutes. Only
act on them during the current scan cycle.
```

### 6D — Update `config/ai_agent.yaml`

- Add `orderbook-liquidity` to `skills.enabled` list, positioned after
  `hyperliquid-microstructure` and before `whale-leaderboard-intel`.
- Remove any reference to `phantom` from `mcp_sources`.
- Replace with `hyperliquid` (direct API adapter from Mission 1).

### Tests for Mission 6

- Confirm all edited/created SKILL.md files load without error via
  `engine/skills.py` skill loader
- Confirm `config/ai_agent.yaml` lists `orderbook-liquidity` and that the
  skill count in the config matches the number of files in `skills/`
- Confirm no reference to `phantom` or `Phantom MCP` remains in any skill
  file or config file

---

## Constraints (carry forward from AGENTS.md)

- Mode remains `live-paper-only`. No live trading, no signing, no fund movement.
- Signal weights in `scoring.py` are FIXED — never adjusted by outcomes.
- No paid API spend without explicit human approval.
- Cron reinstallation requires explicit human approval after adapter changes.
- All new adapters must have `health_check()` and degrade gracefully to empty
  list on failure.
- All new code must pass the full test suite (currently 595 tests passing —
  do not regress).
