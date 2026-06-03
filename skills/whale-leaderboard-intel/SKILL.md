# Whale and Leaderboard Intel

Use whale, leaderboard, or cohort evidence only when it is present in the prompt.

Three independent source tiers provide whale intelligence. Treat each as a
separate evidence stream — overlap between tiers increases conviction.

## Source Tiers

### Tier 1: Dextrabot (wallet leaderboard)

- Delivers individual wallet PnL, win rate, trade count, ROE, and growth rate.
- Entity classification:
  - `smart_money`: Sharpe > 1.5 AND win rate > 55%.
  - `whale_unlabeled`: |PnL| > $10k but insufficient performance stats.
  - `roi_whale`: growth rate > 200% AND trade count > 30 — high-return wallet.
  - `unknown`: insufficient size, label, or performance context.
- Dextrabot data is filtered: 7-day period, $50k+ PnL, 55%+ WR, 30+ trades, SOL-focused.
- Use Dextrabot for directional bias and entity labeling. Do not copy individual trades.

### Tier 2: Hyperdash (whale cohorts)

- Delivers aggregate cohort data for Large Whale ($1M–$5M) and Whale ($500k–$1M) tiers.
- Key metrics per cohort: `whale_cohort_long_pct`, `cohort_direction`, `cohort_oi_usd`.
- Direction thresholds: long if long_pct > 55%, short if < 45%, neutral if 45–55%.
- Hyperdash data is SOL-only.
- Use Hyperdash for consensus direction across large positions. Strongest when both
  cohorts agree (e.g., both > 55% long).

### Tier 3: Hyperliquid whale trades (on-chain)

- Derived from HL open-interest changes and large trade prints inferred from
  the HL orderbook and market data already in the prompt.
- Infer large-position activity from OI spikes + directional price moves.
- This is indirect evidence — less reliable than Tier 1 or Tier 2.
- Use as corroborating context, never as a primary thesis driver.

## Rules

- Never copy trade.
- Treat whale direction as a signal component, not a trade command.
- Downgrade confidence when whale evidence conflicts with price, funding, OI, or liquidity.
- Prefer overlap between independent sources over a single large print.
- If whale data is absent from all three tiers, say so in the rationale or leave it out.
- When all three tiers agree on direction, confidence is highest.
- When tiers conflict, note the disagreement in `risk_notes` and reduce conviction.

