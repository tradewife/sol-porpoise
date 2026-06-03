# Orderbook Liquidity

Interpret orderbook depth, bid/ask wall placement, and book imbalance signals
to assess passive liquidity conditions for trade entry and exit.

## Book Imbalance Signal

The `book_imbalance` signal is extracted from the Hyperliquid L2 orderbook.
It measures the ratio of bid depth to ask depth within 0.5% of the mid price.

### Reading book_imbalance_ratio

| Ratio | Meaning | Trading Implication |
|---|---|---|
| > 1.6 | Strong bid wall — heavy buy-side resting orders within 0.5% of mid | Supports long entries; ask-side liquidity is thin — shorts may face slippage. Stops below the bid wall are safer. |
| 1.3 – 1.6 | Bid-heavy — moderate buy-side dominance | Mild long bias. Corroborate with funding and OI before acting. |
| 0.77 – 1.3 | Balanced — roughly equal bid/ask depth | Neutral liquidity. No directional edge from orderbook alone. |
| 0.60 – 0.77 | Ask-heavy — moderate sell-side dominance | Mild short bias. Corroborate with funding and OI before acting. |
| < 0.60 | Strong ask wall — heavy sell-side resting orders within 0.5% of mid | Supports short entries; bid-side liquidity is thin — longs may face slippage. Stops above the ask wall are safer. |

### Confidence scale

- Strong imbalance (|value| = 2): confidence ≈ 0.9 — highly actionable.
- Moderate imbalance (|value| = 1): confidence ≈ 0.6 — needs corroboration.
- Balanced (value = 0): confidence ≈ 0.3 — not useful as directional evidence.

## Bid/Ask Wall Placement

The prompt may include `bid_wall_05pct` and `ask_wall_05pct` DataPoints:

- **Bid wall**: the largest resting bid order within 0.5% of mid. A large bid wall
  acts as a price floor — aggressive selling is needed to break through.
- **Ask wall**: the largest resting ask order within 0.5% of mid. A large ask wall
  acts as a price ceiling — aggressive buying is needed to break through.

Use wall placement to:
- Estimate where price may encounter resistance or support.
- Set stop-loss levels just beyond the dominant wall.
- Assess whether a breakout is likely to sustain (thin opposing wall) or fail
  (thick opposing wall).

## Liquidation Cluster (Deferred)

The `liquidation_cluster` signal would show where cascading liquidations are
concentrated. This data source is **currently unavailable** (MoonDev Data Layer
endpoints are non-functional).

When liquidation cluster data is absent:
- Do not reject or veto trades based on missing liquidation information.
- Note "liquidation cluster data unavailable" in `data_gaps`.
- Proceed using book imbalance, bid/ask walls, and other available evidence.

When liquidation cluster data becomes available in the future:
- Use cluster zones to identify where forced liquidations may amplify moves.
- Combine with book imbalance: if imbalance direction matches the cluster sweep
  direction, the move is more likely to sustain.
- Place stops beyond cluster zones when entering in the sweep direction.

## Integration with Other Signals

| Signal | How it interacts |
|---|---|
| `funding_stretch` | Strong bid imbalance + stretched positive funding = conflicting signals (bid supports long, funding suggests short lean). Reduce conviction. |
| `oi_delta` | Strong bid imbalance + expanding OI in long direction = high-conviction long setup. |
| `liquidity_magnet` | When book imbalance shows a wall and price is near it, the magnet setup is strongest. |
| `session_structure` | Book imbalance during high-volume sessions is more reliable than during low-volume periods. |
| `volatility` | In high-volatility regimes, orderbook walls break more easily — reduce confidence in wall-based thesis. |
| `whale_evidence` | Whale cohort direction aligned with book imbalance = strongest combination. |

## What this skill does NOT do

- Does not add a new data fetch or adapter.
- Does not replace risk-execution-rails or position sizing.
- Does not veto trades based on orderbook data alone.
- Does not fabricate orderbook data when the HL L2 book is unavailable.
