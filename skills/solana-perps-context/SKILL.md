# Solana Perps Context

When Imperial, Flash Trade, Jupiter, Phoenix, Hyperliquid, or other Solana-native data is present:

- Treat Solana venue data as primary for Solana perps execution context.
- Watch pool utilization and max leverage as liquidity/risk constraints.
- Prefer markets with clean price data, enough liquidity, and coherent funding/volume.
- Consider SOL as a core symbol and compare it against BTC/ETH beta behavior when data allows.
- Flag unavailable Solana DEX TWAP, route cost, or pool data as a confidence haircut, not as bullish or bearish evidence.

Never infer on-chain flow, DEX lead-lag, or wallet labels unless those data points are explicitly present.

