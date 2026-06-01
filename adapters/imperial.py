"""Imperial API adapter: read-only client for api.imperial.space public endpoints."""

from __future__ import annotations

import time
from typing import Any

import httpx

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance, normalize_datapoints, normalize_symbol

BASE_URL = "https://api.imperial.space"
TIMEOUT_S = 30.0


class ImperialAdapter:
    """Read-only client for Imperial API public endpoints."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=TIMEOUT_S)
        self._last_health: AdapterHealth | None = None

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Imperial API",
            source_tier=SourceTier.SOLANA_NATIVE,
            source_link=f"{self.base_url}/api/v1",
            confidence=0.95,
        )

    def health_check(self) -> AdapterHealth:
        try:
            start = time.monotonic()
            r = self._client.get(f"{self.base_url}/api/v1/status")
            latency = (time.monotonic() - start) * 1000
            healthy = r.status_code == 200
            self._last_health = AdapterHealth(
                name="Imperial API",
                healthy=healthy,
                latency_ms=latency,
                last_success_ts=aest_now_iso() if healthy else None,
                last_failure_ts=None if healthy else aest_now_iso(),
            )
        except Exception as e:
            self._last_health = AdapterHealth(
                name="Imperial API",
                healthy=False,
                last_failure_ts=aest_now_iso(),
                error_message=str(e),
            )
        return self._last_health  # type: ignore[return-value]

    async def fetch(self, params: dict[str, Any]) -> list[DataPoint]:
        endpoint = params.get("endpoint", "mark-prices")
        return self._fetch_sync(endpoint, params)

    def _fetch_sync(self, endpoint: str, params: dict[str, Any]) -> list[DataPoint]:
        url = f"{self.base_url}/api/v1/{endpoint}"
        query = {k: v for k, v in params.items() if k != "endpoint" and v is not None}
        r = self._client.get(url, params=query)
        r.raise_for_status()
        data = r.json()
        return self._normalize(endpoint, data)

    # --- Endpoint-specific fetchers ---

    def fetch_mark_prices(self) -> list[DataPoint]:
        return self._fetch_sync("mark-prices", {})

    def fetch_funding_rates(self) -> list[DataPoint]:
        return self._fetch_sync("funding-rates", {})

    def fetch_stats_markets(self) -> list[DataPoint]:
        return self._fetch_sync("stats/markets", {})

    def fetch_open_interest(self) -> list[DataPoint]:
        return self._fetch_sync("stats/open-interest", {})

    def fetch_volume(self) -> list[DataPoint]:
        return self._fetch_sync("stats/volume", {})

    def fetch_oi_history(self, period: str = "24h", grouping: str = "hour") -> list[DataPoint]:
        return self._fetch_sync("stats/open-interest/history", {"period": period, "grouping": grouping})

    def fetch_route(
        self, asset: str, side: str, notional: float, desired_leverage: int
    ) -> list[DataPoint]:
        return self._fetch_sync("route", {
            "asset": asset,
            "side": side,
            "notional": notional,
            "desiredLeverage": desired_leverage,
        })

    def fetch_phoenix_depth(self, asset: str) -> list[DataPoint]:
        return self._fetch_sync("phoenix/depth", {"asset": asset})

    def fetch_gmtrade_liquidity(self) -> list[DataPoint]:
        return self._fetch_sync("gmtrade/liquidity", {})

    def fetch_gmtrade_funding_rates(self) -> list[DataPoint]:
        return self._fetch_sync("gmtrade/funding-rates", {})

    def fetch_status(self) -> list[DataPoint]:
        return self._fetch_sync("status", {})

    # --- Normalization ---

    def _normalize(self, endpoint: str, data: dict[str, Any]) -> list[DataPoint]:
        prov = self.provenance()
        points: list[DataPoint] = []

        if endpoint == "mark-prices":
            rows = data.get("rows", [])
            for row in rows:
                symbol = normalize_symbol(row.get("symbol", ""))
                for venue_key in ("jupiter", "flash", "phoenix", "gmtrade"):
                    venue_data = row.get(venue_key)
                    if venue_data and isinstance(venue_data, dict):
                        price = venue_data.get("price")
                        if price is not None:
                            points.append(DataPoint(
                                symbol=symbol,
                                metric=f"mark_price_{venue_key}",
                                value=float(price),
                                provenance=prov,
                                attrs={"fetchedAtUnixMs": venue_data.get("fetchedAtUnixMs")},
                            ))

        elif endpoint == "funding-rates":
            rows = data.get("rows", [])
            for row in rows:
                symbol = normalize_symbol(row.get("symbol", ""))
                for venue_key in ("jupiter", "flash", "phoenix", "gmtrade"):
                    venue_data = row.get(venue_key)
                    if venue_data and isinstance(venue_data, dict):
                        rate = venue_data.get("fundingRate")
                        if rate is not None:
                            points.append(DataPoint(
                                symbol=symbol,
                                metric=f"funding_rate_{venue_key}",
                                value=float(rate),
                                provenance=prov,
                                attrs={"source": venue_data.get("source", "unknown")},
                            ))

        elif endpoint == "stats/markets":
            rows = data.get("rows", [])
            for row in rows:
                symbol = normalize_symbol(row.get("symbol", ""))
                vol = row.get("volumeUsd")
                oi = row.get("openInterestUsd")
                by_venue = row.get("byVenue", {})
                if vol is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="volume_24h", value=float(vol),
                        provenance=prov, attrs={"byVenue": by_venue},
                    ))
                if oi is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="open_interest", value=float(oi),
                        provenance=prov, attrs={"byVenue": by_venue},
                    ))

        elif endpoint == "stats/open-interest":
            rows = data.get("rows", [])
            for row in rows:
                symbol = normalize_symbol(row.get("symbol", ""))
                oi = row.get("oiUsd") or row.get("openInterestUsd")
                if oi is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="open_interest", value=float(oi), provenance=prov,
                    ))

        elif endpoint == "stats/volume":
            rows = data.get("rows", [])
            for row in rows:
                symbol = normalize_symbol(row.get("symbol", ""))
                vol = row.get("volumeUsd")
                if vol is not None:
                    points.append(DataPoint(
                        symbol=symbol, metric="volume_24h", value=float(vol), provenance=prov,
                    ))

        elif endpoint == "stats/open-interest/history":
            rows = data.get("rows", [])
            for row in rows:
                ts = row.get("timestamp")
                oi = row.get("oiUsd")
                if oi is not None:
                    points.append(DataPoint(
                        symbol="AGGREGATE", metric="oi_history", value=float(oi),
                        provenance=prov, attrs={"timestamp": ts},
                    ))

        elif endpoint == "route":
            venue = data.get("venue")
            cost = data.get("expectedCostUsd")
            breakdown = data.get("costBreakdown", {})
            points.append(DataPoint(
                symbol=normalize_symbol(params := data.get("asset", "")),
                metric="route_cost",
                value=cost,
                provenance=prov,
                attrs={"venue": venue, "costBreakdown": breakdown, "candidates": data.get("candidates", [])},
            ))

        elif endpoint == "status":
            status_val = data.get("status", data)
            points.append(DataPoint(
                symbol="SYSTEM", metric="api_status", value=status_val, provenance=prov,
            ))

        else:
            # Generic: try to extract rows
            rows = data.get("rows", [])
            if isinstance(rows, list):
                for row in rows:
                    symbol = normalize_symbol(row.get("symbol", "UNKNOWN"))
                    points.append(DataPoint(
                        symbol=symbol, metric=endpoint, value=row, provenance=prov,
                    ))

        return points
