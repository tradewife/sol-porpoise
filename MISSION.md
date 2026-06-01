# Mission: Activate the Imperial Agent

Previous mission spec archived at `archive/MISSION.md`.

The scaffold and all engine modules are built (commit `43c8f3a`, 171 tests passing). The remaining work is to replace placeholders with real computation and close the gaps so the agent can produce its first meaningful paper trade.

---

## Current State

| Component | Status |
|-----------|--------|
| Config files | Done |
| Ledger schemas | Done |
| Memory files | Done |
| Report template | Done |
| Dry plumbing run | Done |
| Imperial API adapter | Done (reads mark prices, funding, stats, OI, route) |
| Flash Trade / Phantom MCP adapters | Done (normalization layers) |
| Dextrabot scraper | Done (rate-limited, cached, entity classifier) |
| KG triple writer | Done |
| GraphSignalScore | Done (9 components, weights, unknown handling) |
| Paper order model + fill logic | Done |
| Outcome evaluator | Done |
| Risk sizing | Done |
| Cross-venue basis | Done |
| Hypothesis registry | Done |
| Source health tracker | Done |
| Full scan loop | **Skeleton** -- runs but all signal components are `unknown`, stops use a 2% placeholder, side is guessed from zero-score |
| 14-step report sections | **Partial** -- sections C, D, E are stubs, sections A-B and F-K are populated |

## What Is Not Yet Done

### 1. Volatility and Stop Math

**File**: `engine/volatility.py` (new)

**What**: Compute 1h ATR, realized volatility, and regime classification from OHLCV candles.

**Interface**:
```python
def compute_atr(candles: list[Candle], period: int = 14) -> float
def compute_realized_vol(candles: list[Candle], window: int = 24) -> float
def classify_regime(atr: float, avg_atr: float) -> str  # Quiet/Normal/High/Extreme
def compute_min_stop(atr_1h: float, entry: float, side: OrderSide) -> float
    # max(0.8 * 1h ATR, nearest invalidation)
```

**Data source**: Imperial API candles (if available), or Phantom MCP `perps_markets` with mark-price history. Fall back to recent trade data from mark-price snapshots if candle endpoints are unavailable.

**Integration**: `run_scan.py` Step 6 currently says `stop_pct = 0.02`. Replace with `compute_min_stop(atr, price, side)`.

**Tests**: Unit tests with known candle arrays producing correct ATR, vol, regime, and stop values.

---

### 2. Signal Extraction from Live Data

**File**: `engine/signals.py` (new)

**What**: Extract actual signal values from fetched DataPoints so GraphSignalScore has real inputs instead of all-unknown.

**Signals to extract**:

| Signal | Source | Computation |
|--------|--------|-------------|
| `funding_stretch` | Imperial `funding-rates` | Current rate vs 7-day average. Stretch = `(current - avg) / stdev`. Positive stretch = bearish contrarian for funding fade. |
| `oi_delta` | Imperial `stats/open-interest` + history | 24h OI change % and 1h OI change %. Rising OI + rising price = bullish momentum. Rising OI + falling price = bearish momentum. |
| `basis` | Cross-venue mark prices | `compute_basis()` already exists in `engine/cross_venue.py`. Use it with Imperial vs Hyperliquid prices from Phantom MCP. |
| `liquidity_magnet` | Imperial `phoenix/depth` or orderbook depth | Sum bid/ask depth within 0.5%, 1%, 2% of mid. Identify nearest large resting liquidity cluster. |
| `session_structure` | Imperial candles or mark-price series | Compute VWAP for current session. Identify prior-day VAH/VAL/POC if enough history. |
| `whale_evidence` | Dextrabot + Phantom positions | `integrate_whale_signals()` already exists. Wire it into the scan loop with actual fetched wallet data. |
| `dex_perp_lag` | Imperial vs Phantom price timestamps | Compare last-update timestamps and price levels. If DEX leads perp, note direction. |
| `volatility` | New `engine/volatility.py` | ATR percentile and regime. High vol = wider stops, lower confidence. |
| `catalyst` | Open web scan (future) | For now, hard-code `unknown` with confidence 0. This is the 5% weight component and can wait. |

**Interface**:
```python
def extract_signals(
    symbol: str,
    datapoints: list[DataPoint],
    whale_points: list[DataPoint],
    hl_points: list[DataPoint],
    candles: list[Candle] | None = None,
) -> dict[str, SignalComponent]
```

**Integration**: `run_scan.py` Step 12 currently creates all-unknown components. Replace with `extract_signals()`.

**Tests**: Unit tests with fixture DataPoints producing correct signal values and labels.

---

### 3. Playbook Generation

**File**: `engine/playbooks.py` (new)

**What**: Given a scored symbol with signal components, generate up to 3 playbook candidates with concrete entry/stop/TP levels.

**Setup types** (from MISSION.md):
- `breakout` -- price above/below key level with OI rising
- `fade` -- funding stretch mean reversion
- `vwap_reclaim` -- price reclaiming session VWAP
- `lvn_rejection` -- price rejecting at low volume node
- `liquidity_sweep` -- price sweeping a visible liquidity pool
- `funding_fade` -- fade extreme funding with OI divergence
- `momentum_continuation` -- trend + OI alignment

**Interface**:
```python
def generate_playbooks(
    symbol: str,
    price: float,
    atr: float,
    signals: dict[str, SignalComponent],
    best_bid: float,
    best_ask: float,
) -> list[Playbook]
```

Each `Playbook` has: setup_type, side, entry, stop, tp1, tp2, invalidation, expected_r_r, probability_band, rationale.

**Integration**: `run_scan.py` Step 12 -- replace the current side/stop/TP placeholder logic with playbook output.

**Tests**: Unit tests with known signal inputs producing correct playbook types and levels.

---

### 4. Outcome Evaluation Loop

**File**: `engine/run_scan.py` (extend)

**What**: Implement the `--mode evaluate-outcomes` path in `run_scan.py`.

**Logic**:
1. Read `memory/mission_state.json` for open paper orders.
2. For each open order, fetch current candle data (post-order timestamp).
3. Run `evaluate_fill()` to check fill status.
4. If filled, run `OutcomeEvaluator.compute_outcome()` and write to `outcomes.csv`.
5. If cancel triggered, write cancel outcome with reason.
6. Update `mission_state.json` open/unresolved lists.
7. Compute and update `signal_outcomes.csv` stats.

**Integration**: Wire into `scripts/evaluate_outcomes.sh`.

**Tests**: Integration test: create a paper order, simulate post-order candle data, verify outcome is written correctly.

---

### 5. Live Smoke Test

**What**: Run the agent end-to-end against live APIs and verify it produces a meaningful report.

**Steps**:
1. Run `./scripts/run_scan.sh --mode live-paper`.
2. Verify the Imperial API returns real data (mark prices, funding, stats).
3. Verify the report contains actual prices (not N/A).
4. Verify GraphSignalScore has at least `funding_stretch`, `oi_delta`, and `basis` populated with real values.
5. If the scan produces paper candidates, verify they appear in `ledgers/paper_orders.csv` with valid entries.
6. If no candidates meet the bar, verify the report says `no_trade` with a real reason.

**Gate**: This is the gate that confirms the agent is production-ready for cron scheduling.

---

### 6. Weekly Review Command

**File**: `engine/weekly_review.py` (new)

**What**: Summarize paper-trade performance over the past week.

**Output**:
- Paper-trade expectancy (avg R per trade)
- Drawdown (worst peak-to-trough R)
- Profit factor (gross wins / gross losses)
- Fill rate, cancel rate, no-trade rate
- Per-signal hit rate and avg R (from `signal_outcomes.csv`)
- Top 3 improvement recommendations ranked by expected impact
- One highest-impact implementation pick for next build cycle

**Interface**:
```python
def run_weekly_review(
    outcomes_path: Path,
    signal_outcomes_path: Path,
    paper_orders_path: Path,
) -> WeeklyReviewResult
```

**Integration**: `scripts/weekly_review.sh` (new), cron-ready.

**Tests**: Unit tests with fixture outcome data producing correct metrics.

---

## Milestones

### Milestone A: Volatility + Signal Extraction

**Deliver**: `engine/volatility.py`, `engine/signals.py`, integration into `run_scan.py` replacing placeholders. Real ATR-based stops, real signal component values for funding, OI, basis, volatility.

**Validation**: `pytest tests/ -v` passes. A live-paper run produces a report with at least 4 non-unknown signal components per scored symbol.

### Milestone B: Playbooks + Complete Scan Loop

**Deliver**: `engine/playbooks.py`, full integration. Sections C, D, E of the report contain real evidence tables. Side selection is driven by signal direction, not guesswork.

**Validation**: Report sections C, D, E contain actual data, not stubs.

### Milestone C: Outcome Evaluation + Weekly Review

**Deliver**: `--mode evaluate-outcomes` path, `engine/weekly_review.py`, `scripts/weekly_review.sh`.

**Validation**: Can create a paper order, evaluate it against subsequent data, and produce an outcome row.

### Milestone D: Live Smoke Test

**Deliver**: Successful end-to-end run against live Imperial API with real data in the report.

**Validation**: Report contains real prices, real funding rates, real OI, at least one scored candidate (even if no trade is the final decision).

## Operating Constraints (unchanged)

- Default mode is `live-paper-only`.
- Do not place live orders, sign transactions, move funds, or spend paid API budget without explicit human approval.
- Paper trades must be generated from live market snapshots before outcomes occur.
- Every metric row must carry provenance.
- Mode is read from `memory/mission_state.json` at the start of every run.
