"""Dextrabot adapter: JSON API client for whale/smart-money wallet intelligence.

API Discovery (2026-06-03):
  Discovered via agent-browser Network tab on app.dextrabot.com/discover-wallets.
  The SPA frontend calls a backend API at dextradata.nftinit.io which returns
  paginated JSON with full wallet metrics (PnL, sharpe, win rate, growth rate,
  drawdown, trade counts, open positions, etc.).

  Endpoint: GET https://dextradata.nftinit.io/api/hyper/get_wallets_profit_new/
  Auth: none (public)
  Response: {"count": N, "next": "...", "previous": null, "results": [...]}
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance


# ---------------------------------------------------------------------------
# API constants
# ---------------------------------------------------------------------------

DEXT_BASE_URL = "https://app.dextrabot.com"
DEXT_API_URL = "https://dextradata.nftinit.io/api/hyper/get_wallets_profit_new/"

# Discovered JSON API endpoint — replaces HTML scraping.
# Public, no auth required. Paginated JSON response.
DEXT_API_NOTE = (
    "JSON API discovered 2026-06-03 via agent-browser. "
    "Endpoint: GET https://dextradata.nftinit.io/api/hyper/get_wallets_profit_new/ "
    "Params: period (days), order (-perp_pnl|-margin_roi), coin, min_pnl, "
    "min_win_complated_rate, min_complated_trades_count, offset, limit. "
    "Response: {count, next, previous, results: [{user_token, "
    "portfolio_perp_week_pnl, portfolio_perp_week_sharpe, total_win_rate, "
    "margin_roi, avg_uleverage_value, portfolio_perp_week_growth_rate, "
    "portfolio_perp_week_dd, rtx_count, complated_trades_count, "
    "win_complated_rate, open_positions, ...}]}"
)

# Default filter parameters for optimal whale discovery
DEFAULT_FILTERS: dict[str, Any] = {
    "period": 7,          # 7D lookback
    "order": "-margin_roi",  # sort by ROE descending
    "coin": "SOL",        # SOL-focused
    "min_pnl": 50000,     # minimum $50k PnL
    "min_win_complated_rate": 55,  # minimum 55% win rate
    "min_complated_trades_count": 30,  # minimum 30 completed trades
    "offset": 0,
    "limit": 50,
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WalletData:
    address: str
    pnl: float | None = None
    unrealized_pnl: float | None = None
    win_rate: float | None = None
    sharpe: float | None = None
    leverage: float | None = None
    growth_rate: float | None = None
    drawdown: float | None = None
    tx_count: int | None = None
    token_breakdown: list[dict[str, Any]] = field(default_factory=list)
    entity_type: str = "unknown"  # smart_money, whale_unlabeled, roi_whale, unknown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RateLimiter:
    def __init__(self, max_requests: int = 10, per_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self._timestamps: list[float] = []

    def wait(self) -> None:
        now = time.monotonic()
        self._timestamps = [t for t in self._timestamps if now - t < self.per_seconds]
        if len(self._timestamps) >= self.max_requests:
            sleep_time = self._timestamps[0] + self.per_seconds - now
            if sleep_time > 0:
                time.sleep(sleep_time)
        self._timestamps.append(time.monotonic())


class ResponseCache:
    def __init__(self, cache_dir: str | Path = "data/raw", ttl_seconds: int = 300) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _cache_key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def get(self, url: str) -> str | None:
        path = self.cache_dir / f"{self._cache_key(url)}.json"
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age < self.ttl_seconds:
                return path.read_text(encoding="utf-8")
        return None

    def put(self, url: str, content: str) -> None:
        path = self.cache_dir / f"{self._cache_key(url)}.json"
        path.write_text(content, encoding="utf-8")


def classify_entity(wallet: WalletData) -> str:
    """Classify wallet as smart_money, whale_unlabeled, roi_whale, or unknown.

    Priority order:
      1. smart_money  — sharpe > 1.5 AND win_rate > 55
      2. roi_whale    — growth_rate > 200 AND tx_count > 30
      3. whale_unlabeled — |pnl| > 10000
      4. unknown
    """
    if wallet.sharpe is not None and wallet.sharpe > 1.5:
        if wallet.win_rate is not None and wallet.win_rate > 55:
            return "smart_money"
    if wallet.growth_rate is not None and wallet.growth_rate > 200:
        if wallet.tx_count is not None and wallet.tx_count > 30:
            return "roi_whale"
    if wallet.pnl is not None and abs(wallet.pnl) > 10000:
        return "whale_unlabeled"
    return "unknown"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class DextrabotAdapter:
    """JSON API client for Dextrabot whale/smart-money wallet intelligence.

    Uses direct JSON API at dextradata.nftinit.io (discovered 2026-06-03)
    instead of HTML scraping. Falls back gracefully on empty/error responses.
    """

    def __init__(
        self,
        base_url: str = DEXT_BASE_URL,
        api_url: str = DEXT_API_URL,
        cache_dir: str | Path = "data/raw",
        cache_ttl: int = 300,
        rate_limit: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = api_url.rstrip("/")
        self.rate_limiter = RateLimiter(max_requests=rate_limit)
        self.cache = ResponseCache(cache_dir, ttl_seconds=cache_ttl)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "ImperialAgent/0.1",
            "Accept": "application/json",
        })

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Dextrabot",
            source_tier=SourceTier.OPEN,
            source_link=self.api_url,
            confidence=0.70,
        )

    def health_check(self) -> AdapterHealth:
        try:
            r = self._session.get(self.api_url, params={"limit": 1}, timeout=10)
            return AdapterHealth(
                name="Dextrabot", healthy=r.status_code == 200,
                last_success_ts=aest_now_iso(),
            )
        except Exception as e:
            return AdapterHealth(
                name="Dextrabot", healthy=False,
                last_failure_ts=aest_now_iso(), error_message=str(e),
            )

    async def fetch(self, params: dict[str, Any]) -> list[DataPoint]:
        return self.fetch_wallets(**params)

    def fetch_wallets(
        self,
        coin: str | None = None,
        period: str = "7D",
        min_pnl: float | None = None,
        min_win_rate: float | None = None,
        min_trades: int | None = None,
        sort_by: str = "roe",
        limit: int = 50,
    ) -> list[DataPoint]:
        """Fetch wallet data from Dextrabot JSON API.

        Uses optimal filter params by default:
          period=7D, min_pnl=50000, min_win_rate=55, min_trades=30,
          sort_by=roe, coin=SOL.

        Args:
            coin: Filter by coin (e.g. "SOL"). Defaults to "SOL" via DEFAULT_FILTERS.
            period: Lookback period (e.g. "7D" → API uses 7 days). Defaults to "7D".
            min_pnl: Minimum PnL filter. Defaults to 50000.
            min_win_rate: Minimum win rate filter. Defaults to 55.
            min_trades: Minimum completed trades filter. Defaults to 30.
            sort_by: Sort order — "roe" maps to -margin_roi, "pnl" to -perp_pnl.
            limit: Max number of results to return.
        """
        # Parse period string to days integer for the API
        period_days = self._parse_period(period)

        # Build API query params with optimal defaults
        params: dict[str, Any] = dict(DEFAULT_FILTERS)
        params["period"] = period_days
        params["coin"] = coin or DEFAULT_FILTERS["coin"]
        params["limit"] = limit

        if min_pnl is not None:
            params["min_pnl"] = min_pnl
        if min_win_rate is not None:
            params["min_win_complated_rate"] = min_win_rate
        if min_trades is not None:
            params["min_complated_trades_count"] = min_trades

        # Map sort_by to API order param
        params["order"] = self._map_sort(sort_by)

        # Remove empty-string params (API uses absent params vs empty)
        clean_params = {k: v for k, v in params.items() if v not in (None, "")}

        # Build cache key from the request URL
        cache_key = self.api_url + "?" + "&".join(
            f"{k}={v}" for k, v in sorted(clean_params.items())
        )

        # Try cache first
        cached = self.cache.get(cache_key)
        if cached is not None:
            try:
                data = json.loads(cached)
                return self._parse_api_response(data)
            except (json.JSONDecodeError, KeyError):
                pass  # Fall through to live fetch

        # Live API request
        self.rate_limiter.wait()
        try:
            r = self._session.get(self.api_url, params=clean_params, timeout=30)
            r.raise_for_status()
            data = r.json()
            self.cache.put(cache_key, r.text)
            return self._parse_api_response(data)
        except Exception:
            return []

    def _parse_api_response(self, data: dict[str, Any]) -> list[DataPoint]:
        """Parse the JSON API response into DataPoints."""
        results = data.get("results", [])
        if not results:
            return []

        wallets: list[WalletData] = []
        for entry in results:
            wallet = self._parse_wallet_entry(entry)
            if wallet is not None:
                wallets.append(wallet)

        return self._wallets_to_datapoints(wallets)

    def _parse_wallet_entry(self, entry: dict[str, Any]) -> WalletData | None:
        """Parse a single API result entry into WalletData.

        API field mapping:
          user_token → address
          portfolio_perp_week_pnl → pnl (period-aware)
          total_unrealized_pnl → unrealized_pnl
          win_complated_rate or total_win_rate → win_rate
          portfolio_perp_week_sharpe → sharpe
          avg_uleverage_value → leverage
          portfolio_perp_week_growth_rate → growth_rate
          portfolio_perp_week_dd → drawdown
          rtx_count → tx_count
        """
        address = entry.get("user_token", "unknown")
        if address == "unknown":
            return None

        # Win rate: prefer completed win rate, fall back to total
        win_rate = self._safe_float(
            entry.get("win_complated_rate") or entry.get("total_win_rate")
        )

        wallet = WalletData(
            address=address,
            pnl=self._safe_float(entry.get("portfolio_perp_week_pnl")),
            unrealized_pnl=self._safe_float(entry.get("total_unrealized_pnl")),
            win_rate=win_rate,
            sharpe=self._safe_float(entry.get("portfolio_perp_week_sharpe")),
            leverage=self._safe_float(entry.get("avg_uleverage_value")),
            growth_rate=self._safe_float(entry.get("portfolio_perp_week_growth_rate")),
            drawdown=self._safe_float(entry.get("portfolio_perp_week_dd")),
            tx_count=self._safe_int(entry.get("rtx_count")),
        )
        wallet.entity_type = classify_entity(wallet)
        return wallet

    def _wallets_to_datapoints(self, wallets: list[WalletData]) -> list[DataPoint]:
        prov = self.provenance()
        points: list[DataPoint] = []
        for w in wallets:
            attrs: dict[str, Any] = {
                "entity_type": w.entity_type,
                "address": w.address[:12] + "..." if len(w.address) > 12 else w.address,
            }
            if w.sharpe is not None:
                attrs["sharpe"] = w.sharpe
            if w.win_rate is not None:
                attrs["win_rate"] = w.win_rate
            if w.leverage is not None:
                attrs["leverage"] = w.leverage
            if w.growth_rate is not None:
                attrs["growth_rate"] = w.growth_rate
            if w.tx_count is not None:
                attrs["tx_count"] = w.tx_count

            if w.pnl is not None:
                points.append(DataPoint(
                    symbol="HYPERLIQUID",
                    metric="whale_pnl",
                    value=w.pnl,
                    provenance=prov,
                    attrs=attrs,
                ))
        return points

    @staticmethod
    def _parse_period(period: str) -> int:
        """Parse period string (e.g. '7D', '30D', '1D') to days integer."""
        match = re.match(r"(\d+)[Dd]?", period)
        if match:
            return int(match.group(1))
        return 7  # default to 7 days

    @staticmethod
    def _map_sort(sort_by: str) -> str:
        """Map sort_by name to API order parameter."""
        mapping = {
            "roe": "-margin_roi",
            "pnl": "-perp_pnl",
            "sharpe": "-perp_sharpe",
            "win_rate": "-win_complated_rate",
            "growth_rate": "-perp_growth_rate",
            "trades": "-complated_trades_count",
        }
        return mapping.get(sort_by, "-margin_roi")

    @staticmethod
    def _safe_float(val: Any) -> float | None:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val: Any) -> int | None:
        if val is None:
            return None
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None
