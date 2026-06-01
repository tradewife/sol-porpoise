"""Flash Trade MCP adapter: wraps Flash Trade MCP tools into DataPoints."""

from __future__ import annotations

from typing import Any

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance, normalize_symbol


class FlashTradeAdapter:
    """Wraps Flash Trade MCP tool calls and returns normalized DataPoints.

    NOTE: Actual MCP calls happen outside Python. This adapter provides
    the normalization layer for data received via MCP tools in session.
    """

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Flash Trade MCP",
            source_tier=SourceTier.SOLANA_NATIVE,
            source_link="[no-link]",
            confidence=0.90,
        )

    def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            name="Flash Trade MCP",
            healthy=True,
            last_success_ts=aest_now_iso(),
        )

    async def fetch(self, params: dict[str, Any]) -> list[DataPoint]:
        data = params.get("data", {})
        data_type = params.get("data_type", "trading_overview")
        return self._normalize(data_type, data)

    def normalize_trading_overview(self, data: list[dict[str, Any]]) -> list[DataPoint]:
        """Normalize trading overview from get_trading_overview MCP response."""
        return self._normalize("trading_overview", {"markets": data})

    def normalize_prices(self, data: dict[str, Any]) -> list[DataPoint]:
        """Normalize price data from get_prices MCP response."""
        return self._normalize("prices", data)

    def _normalize(self, data_type: str, data: dict[str, Any] | list[dict[str, Any]]) -> list[DataPoint]:
        prov = self.provenance()
        points: list[DataPoint] = []

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("markets", data.get("rows", [data]))
        else:
            return points

        for item in items:
            if not isinstance(item, dict):
                continue
            raw_symbol = item.get("symbol", item.get("name", "UNKNOWN"))
            symbol = normalize_symbol(raw_symbol)

            if data_type == "trading_overview":
                price = item.get("price") or item.get("markPrice")
                if price is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="mark_price_flash", value=float(price), provenance=prov,
                    ))
                max_lev = item.get("maxLeverage") or item.get("max_leverage")
                if max_lev is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="max_leverage_flash", value=float(max_lev), provenance=prov,
                    ))
                pool_util = item.get("poolUtilization") or item.get("pool_utilization")
                if pool_util is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="pool_utilization_flash", value=float(pool_util), provenance=prov,
                    ))

            elif data_type == "prices":
                price = item.get("price") or item.get("value")
                if price is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="price_flash", value=float(price), provenance=prov,
                    ))

        return points
