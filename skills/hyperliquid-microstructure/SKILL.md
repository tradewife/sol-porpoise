# Hyperliquid Microstructure

When Hyperliquid or cross-venue perps data is present, evaluate:

- Funding stretch and whether it aligns or conflicts with basis.
- Open-interest expansion or contraction against price direction.
- Volume concentration and whether Hyperliquid appears to lead or lag.
- Passive entry feasibility relative to the current price proxy in the prompt.
- Whether stop distance is large enough for the current volatility proxy.
- Book imbalance (bid/ask depth ratio) as a directional bias indicator.

## Liquidation Guidance (Conditional)

Liquidation data is **guidance only** — never an unconditional veto.

- If `liquidation_cluster` data is available in the prompt, use it to gauge
  where cascading liquidations may amplify moves. Place stops beyond cluster
  zones when entering in the same direction as the cluster sweep.
- If `liquidation_cluster` data is **not** available (currently deferred),
  do not reject or block any trade. Simply note "liquidation data unavailable"
  in `data_gaps` and proceed with the remaining evidence.
- If `book_imbalance_ratio` shows heavy bid-side imbalance (>1.3) near a
  known liquidation cluster zone, the cluster is more likely to be swept —
  treat as supportive evidence for long momentum, not as a reason to fade.

## Book Imbalance Integration

The `book_imbalance` signal provides an orderbook depth reading:

| book_imbalance_ratio | Reading | Action |
|---|---|---|
| > 1.6 | Strong bid wall | Favors longs; aggressive entries justified |
| 1.3 – 1.6 | Bid-heavy | Mild long bias; corroborate with OI/funding |
| 0.77 – 1.3 | Balanced | Neutral; rely on other signals |
| 0.60 – 0.77 | Ask-heavy | Mild short bias; corroborate with OI/funding |
| < 0.60 | Strong ask wall | Favors shorts; aggressive entries justified |

Use book imbalance to confirm or challenge the thesis from funding, OI, and
volume — never as a standalone signal.

## Setup Patterns

- `funding_fade`: stretched funding plus weak continuation evidence.
- `momentum_continuation`: OI and volume expanding with trend.
- `liquidity_sweep`: price near a likely stop/magnet area with reversal evidence.
- `liquidity_magnet`: strong book imbalance (>1.6 or <0.60) near a key level
  with OI expanding in the same direction — the orderbook wall is likely to
  attract price and then break through, creating a momentum burst.
- `fade`: stretched move without supportive OI/volume.

Reject setups where the thesis depends on unavailable CVD or stale whale claims.

