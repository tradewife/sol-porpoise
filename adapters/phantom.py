"""Phantom MCP adapter: wraps Phantom MCP tools for Hyperliquid reference data."""

from __future__ import annotations

from typing import Any

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance, normalize_symbol


class PhantomAdapter:
    """Wraps Phantom MCP tool calls and returns normalized DataPoints for Hyperliquid data.

    NOTE: Actual MCP calls happen outside Python. This adapter provides
    the normalization layer for data received via Phantom MCP tools.
    """

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Phantom MCP (Hyperliquid)",
            source_tier=SourceTier.HL_NATIVE,
            source_link="[no-link]",
            confidence=0.90,
        )

    def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            name="Phantom MCP",
            healthy=True,
            last_success_ts=aest_now_iso(),
        )

    async def fetch(self, params: dict[str, Any]) -> list[DataPoint]:
        data = params.get("data", {})
        data_type = params.get("data_type", "markets")
        return self._normalize(data_type, data)

    def normalize_markets(self, data: list[dict[str, Any]]) -> list[DataPoint]:
        """Normalize perps_markets MCP response."""
        return self._normalize("markets", {"rows": data})

    def normalize_positions(self, data: list[dict[str, Any]]) -> list[DataPoint]:
        """Normalize perps_positions MCP response."""
        return self._normalize("positions", {"rows": data})

    def _normalize(self, data_type: str, data: dict[str, Any] | list[dict[str, Any]]) -> list[DataPoint]:
        prov = self.provenance()
        points: list[DataPoint] = []

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("rows", [data])
        else:
            return points

        for item in items:
            if not isinstance(item, dict):
                continue
            raw_coin = item.get("coin", item.get("symbol", item.get("name", "UNKNOWN")))
            symbol = normalize_symbol(raw_coin)

            if data_type == "markets":
                price = item.get("markPx") or item.get("markPrice") or item.get("price")
                if price is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="mark_price_hl", value=float(price), provenance=prov,
                    ))
                funding = item.get("funding") or item.get("fundingRate")
                if funding is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="funding_rate_hl", value=float(funding), provenance=prov,
                    ))
                oi = item.get("openInterest") or item.get("oi")
                if oi is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="open_interest_hl", value=float(oi), provenance=prov,
                    ))
                lev = item.get("maxLeverage") or item.get("max_leverage")
                if lev is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="max_leverage_hl", value=float(lev), provenance=prov,
                    ))

            elif data_type == "positions":
                entry = item.get("entryPx") or item.get("entryPrice")
                size = item.get("size") or item.get("positionValue")
                if entry is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="hl_position_entry", value=float(entry), provenance=prov,
                        attrs={
                            "direction": item.get("direction", item.get("side", "unknown")),
                            "size": float(size) if size else None,
                            "unrealizedPnl": item.get("unrealizedPnl"),
                        },
                    ))

        return points
