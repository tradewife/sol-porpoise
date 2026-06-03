"""Hyperliquid direct HTTP adapter: direct POST to api.hyperliquid.xyz/info for market data."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

import httpx

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance, normalize_symbol

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hyperliquid.xyz"
INFO_ENDPOINT = "/info"
TIMEOUT_S = 30.0


class HyperliquidAdapter:
    """Direct HTTP adapter for Hyperliquid public info API.

    All calls are POST to https://api.hyperliquid.xyz/info with JSON body
    specifying the request type. No authentication required.

    Implements DataAdapter protocol: fetch(), provenance(), health_check().
    """

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=TIMEOUT_S)
        self._last_health: AdapterHealth | None = None

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Hyperliquid API",
            source_tier=SourceTier.HL_NATIVE,
            source_link=f"{self.base_url}{INFO_ENDPOINT}",
            confidence=0.92,
        )

    def health_check(self) -> AdapterHealth:
        try:
            start = time.monotonic()
            r = self._client.post(
                f"{self.base_url}{INFO_ENDPOINT}",
                json={"type": "metaAndAssetCtxs"},
            )
            latency = (time.monotonic() - start) * 1000
            healthy = r.status_code == 200
            self._last_health = AdapterHealth(
                name="Hyperliquid",
                healthy=healthy,
                latency_ms=latency,
                last_success_ts=aest_now_iso() if healthy else None,
                last_failure_ts=None if healthy else aest_now_iso(),
                error_message=None if healthy else f"HTTP {r.status_code}",
            )
        except Exception as e:
            self._last_health = AdapterHealth(
                name="Hyperliquid",
                healthy=False,
                last_failure_ts=aest_now_iso(),
                error_message=str(e),
            )
        return self._last_health  # type: ignore[return-value]

    async def fetch(self, params: dict[str, Any]) -> list[DataPoint]:
        """Generic fetch dispatcher. params must contain 'method' key."""
        method = params.get("method", "markets")
        if method == "markets":
            return self.fetch_markets()
        elif method == "orderbook":
            coin = params.get("coin", "SOL")
            return self.fetch_orderbook(coin=coin)
        elif method == "candles":
            coin = params.get("coin", "SOL")
            interval = params.get("interval", "1h")
            return self.fetch_candles(coin=coin, interval=interval)
        return []

    # ------------------------------------------------------------------
    # Core fetch methods
    # ------------------------------------------------------------------

    def fetch_markets(self) -> list[DataPoint]:
        """Fetch market data via metaAndAssetCtxs endpoint.

        Returns DataPoints for: mark_price_hl, funding_rate_hl,
        open_interest_hl, basis_hl, max_leverage_hl.
        """
        try:
            r = self._client.post(
                f"{self.base_url}{INFO_ENDPOINT}",
                json={"type": "metaAndAssetCtxs"},
            )
            r.raise_for_status()
            data = r.json()
            return self._normalize_markets(data)
        except Exception as e:
            logger.warning("Hyperliquid fetch_markets failed: %s", e)
            return []

    def fetch_orderbook(self, coin: str = "SOL") -> list[DataPoint]:
        """Fetch L2 orderbook and compute bid/ask walls + imbalance ratio.

        Returns DataPoints for: bid_wall_05pct, ask_wall_05pct,
        book_imbalance_ratio.
        """
        try:
            r = self._client.post(
                f"{self.base_url}{INFO_ENDPOINT}",
                json={"type": "l2Book", "coin": coin, "nSigFigs": 4},
            )
            r.raise_for_status()
            data = r.json()
            return self._normalize_orderbook(coin, data)
        except Exception as e:
            logger.warning("Hyperliquid fetch_orderbook failed: %s", e)
            return []

    def fetch_candles(
        self, coin: str = "SOL", interval: str = "1h", hours: int = 168
    ) -> list[dict[str, Any]]:
        """Fetch candle snapshots via candleSnapshot endpoint.

        Returns raw candle dicts with keys: t, o, h, l, c, v in ascending
        time order. Each candle also has T, s, i, n from the API.
        """
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - hours * 3600 * 1000
            r = self._client.post(
                f"{self.base_url}{INFO_ENDPOINT}",
                json={
                    "type": "candleSnapshot",
                    "req": {
                        "coin": coin,
                        "interval": interval,
                        "startTime": start_ms,
                        "endTime": now_ms,
                    },
                },
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                return []
            # Normalize to consistent types and sort ascending by t
            candles: list[dict[str, Any]] = []
            for c in data:
                if not isinstance(c, dict):
                    continue
                try:
                    candles.append({
                        "t": int(c.get("t", 0)),
                        "o": float(c.get("o", 0)),
                        "h": float(c.get("h", 0)),
                        "l": float(c.get("l", 0)),
                        "c": float(c.get("c", 0)),
                        "v": float(c.get("v", 0)),
                        # Keep extra fields for downstream use
                        "T": int(c.get("T", 0)),
                        "s": str(c.get("s", "")),
                        "i": str(c.get("i", "")),
                        "n": int(c.get("n", 0)),
                    })
                except (TypeError, ValueError):
                    continue
            candles.sort(key=lambda x: x["t"])
            return candles
        except Exception as e:
            logger.warning("Hyperliquid fetch_candles failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_markets(self, data: Any) -> list[DataPoint]:
        """Parse metaAndAssetCtxs response into DataPoints.

        Expected format: [meta_dict, asset_ctxs_list]
        meta_dict.universe = list of asset dicts with name, maxLeverage, etc.
        asset_ctxs_list = parallel list of context dicts with markPx, funding, etc.
        """
        if not isinstance(data, list) or len(data) < 2:
            return []

        meta = data[0]
        asset_ctxs = data[1]

        if not isinstance(meta, dict) or not isinstance(asset_ctxs, list):
            return []

        universe = meta.get("universe", [])
        if not isinstance(universe, list):
            return []

        prov = self.provenance()
        points: list[DataPoint] = []

        for i, asset_meta in enumerate(universe):
            if not isinstance(asset_meta, dict):
                continue
            raw_coin = asset_meta.get("name", "UNKNOWN")
            symbol = normalize_symbol(raw_coin)
            max_lev = asset_meta.get("maxLeverage")

            ctx = asset_ctxs[i] if i < len(asset_ctxs) else {}
            if not isinstance(ctx, dict):
                continue

            # mark_price_hl
            mark_px = ctx.get("markPx")
            if mark_px is not None:
                try:
                    points.append(DataPoint(
                        symbol=symbol, metric="mark_price_hl",
                        value=float(mark_px), provenance=prov,
                    ))
                except (TypeError, ValueError):
                    pass

            # funding_rate_hl
            funding = ctx.get("funding")
            if funding is not None:
                try:
                    points.append(DataPoint(
                        symbol=symbol, metric="funding_rate_hl",
                        value=float(funding), provenance=prov,
                    ))
                except (TypeError, ValueError):
                    pass

            # open_interest_hl
            oi = ctx.get("openInterest")
            if oi is not None:
                try:
                    points.append(DataPoint(
                        symbol=symbol, metric="open_interest_hl",
                        value=float(oi), provenance=prov,
                    ))
                except (TypeError, ValueError):
                    pass

            # basis_hl: (markPx - oraclePx) / oraclePx
            oracle_px = ctx.get("oraclePx")
            if mark_px is not None and oracle_px is not None:
                try:
                    mp = float(mark_px)
                    op = float(oracle_px)
                    if op != 0:
                        basis = (mp - op) / op
                        points.append(DataPoint(
                            symbol=symbol, metric="basis_hl",
                            value=basis, provenance=prov,
                        ))
                except (TypeError, ValueError):
                    pass

            # max_leverage_hl
            if max_lev is not None:
                try:
                    points.append(DataPoint(
                        symbol=symbol, metric="max_leverage_hl",
                        value=float(max_lev), provenance=prov,
                    ))
                except (TypeError, ValueError):
                    pass

        return points

    def _normalize_orderbook(self, coin: str, data: Any) -> list[DataPoint]:
        """Parse l2Book response into orderbook DataPoints.

        Expected format: {coin, time, levels: [[bids], [asks]]}
        Each level: {px, sz, n}
        """
        if not isinstance(data, dict):
            return []

        levels = data.get("levels", [])
        if not isinstance(levels, list) or len(levels) < 2:
            return []

        bids = levels[0]
        asks = levels[1]
        if not isinstance(bids, list) or not isinstance(asks, list):
            return []

        symbol = normalize_symbol(coin)
        prov = self.provenance()
        points: list[DataPoint] = []

        # Find mid price from best bid/ask
        best_bid = float(bids[0]["px"]) if bids and "px" in bids[0] else None
        best_ask = float(asks[0]["px"]) if asks and "px" in asks[0] else None

        if best_bid is None or best_ask is None:
            return []

        mid = (best_bid + best_ask) / 2.0
        threshold = mid * 0.005  # 0.5% of mid

        # Sum bid and ask sizes within 0.5% of mid
        bid_wall_size = 0.0
        for level in bids:
            try:
                px = float(level.get("px", 0))
                sz = float(level.get("sz", 0))
                if mid - px <= threshold and px > 0:
                    bid_wall_size += sz
            except (TypeError, ValueError):
                continue

        ask_wall_size = 0.0
        for level in asks:
            try:
                px = float(level.get("px", 0))
                sz = float(level.get("sz", 0))
                if px - mid <= threshold and px > 0:
                    ask_wall_size += sz
            except (TypeError, ValueError):
                continue

        # bid_wall_05pct
        points.append(DataPoint(
            symbol=symbol, metric="bid_wall_05pct",
            value=bid_wall_size, provenance=prov,
            attrs={"threshold_pct": 0.5, "mid": mid},
        ))

        # ask_wall_05pct
        points.append(DataPoint(
            symbol=symbol, metric="ask_wall_05pct",
            value=ask_wall_size, provenance=prov,
            attrs={"threshold_pct": 0.5, "mid": mid},
        ))

        # book_imbalance_ratio: bid_wall / ask_wall
        if ask_wall_size > 0:
            ratio = bid_wall_size / ask_wall_size
        else:
            ratio = float("inf") if bid_wall_size > 0 else 1.0

        if not math.isfinite(ratio):
            ratio = 1.0  # degenerate case → neutral

        points.append(DataPoint(
            symbol=symbol, metric="book_imbalance_ratio",
            value=ratio, provenance=prov,
            attrs={
                "bid_wall_size": bid_wall_size,
                "ask_wall_size": ask_wall_size,
                "mid": mid,
                "threshold_pct": 0.5,
            },
        ))

        return points
