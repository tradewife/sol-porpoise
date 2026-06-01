# Adapter Registry

Available data adapters, source quality, known quirks, and fallback order.

## Registered Adapters

| Adapter | Source | Tier | Status | Quirks |
|---------|--------|------|--------|--------|
| ImperialAdapter | Imperial API (api.imperial.space) | Open | pending | Public REST, no auth for reads |
| FlashTradeAdapter | Flash Trade MCP | Solana-native | pending | In-session MCP |
| PhantomAdapter | Phantom MCP (Hyperliquid) | HL-native | pending | In-session MCP, data-only reference |
| DextrabotAdapter | Dextrabot (app.dextrabot.com) | Open | pending | Web scraping, no public API |

## Source Quality Notes

_No quality observations yet. Notes will be added as adapters are exercised in live runs._

## Fallback Order

When a higher-tier source is unavailable, record the failure and fall back in this order:

1. **Imperial API** (primary market data, venue routing)
2. **Flash Trade MCP** (supplementary Solana perp data)
3. **Phantom MCP / Hyperliquid** (cross-venue reference data)
4. **Dextrabot** (whale/smart-money intelligence)

Missing sources degrade confidence instead of blocking unrelated scans.
