# Hyperliquid Microstructure

When Hyperliquid or cross-venue perps data is present, evaluate:

- Funding stretch and whether it aligns or conflicts with basis.
- Open-interest expansion or contraction against price direction.
- Volume concentration and whether Hyperliquid appears to lead or lag.
- Passive entry feasibility relative to the current price proxy in the prompt.
- Whether stop distance is large enough for the current volatility proxy.

Useful setup patterns:

- `funding_fade`: stretched funding plus weak continuation evidence.
- `momentum_continuation`: OI and volume expanding with trend.
- `liquidity_sweep`: price near a likely stop/magnet area with reversal evidence.
- `fade`: stretched move without supportive OI/volume.

Reject setups where the thesis depends on unavailable liquidation maps, unavailable CVD, or stale whale claims.

