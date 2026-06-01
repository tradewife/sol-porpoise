"""Dextrabot scraper adapter: web scraping with rate limiting and caching."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from adapters.base import AdapterHealth, DataPoint, Provenance, SourceTier
from adapters.normalizer import aest_now_iso, make_provenance


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
    entity_type: str = "unknown"  # smart_money, whale_unlabeled, unknown


DEXT_BASE_URL = "https://app.dextrabot.com"


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
        path = self.cache_dir / f"{self._cache_key(url)}.html"
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age < self.ttl_seconds:
                return path.read_text(encoding="utf-8")
        return None

    def put(self, url: str, content: str) -> None:
        path = self.cache_dir / f"{self._cache_key(url)}.html"
        path.write_text(content, encoding="utf-8")


def classify_entity(wallet: WalletData) -> str:
    """Classify wallet as smart_money, whale_unlabeled, or unknown."""
    if wallet.sharpe is not None and wallet.sharpe > 1.5:
        if wallet.win_rate is not None and wallet.win_rate > 55:
            return "smart_money"
    if wallet.pnl is not None and abs(wallet.pnl) > 10000:
        return "whale_unlabeled"
    return "unknown"


class DextrabotAdapter:
    """Scrapes Dextrabot for whale/smart-money wallet intelligence."""

    def __init__(
        self,
        base_url: str = DEXT_BASE_URL,
        cache_dir: str | Path = "data/raw",
        cache_ttl: int = 300,
        rate_limit: int = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = RateLimiter(max_requests=rate_limit)
        self.cache = ResponseCache(cache_dir, ttl_seconds=cache_ttl)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "ImperialAgent/0.1"})

    def provenance(self) -> Provenance:
        return make_provenance(
            source_name="Dextrabot",
            source_tier=SourceTier.OPEN,
            source_link=self.base_url,
            confidence=0.70,
        )

    def health_check(self) -> AdapterHealth:
        try:
            r = self._session.get(self.base_url, timeout=10)
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
    ) -> list[DataPoint]:
        """Fetch and parse wallet data from Dextrabot discover-wallets page."""
        url = f"{self.base_url}/discover-wallets"
        params: dict[str, str] = {}
        if coin:
            params["coin"] = coin.upper()
        if period:
            params["period"] = period

        cache_key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        html = self.cache.get(cache_key)

        if html is None:
            self.rate_limiter.wait()
            try:
                r = self._session.get(url, params=params, timeout=30)
                r.raise_for_status()
                html = r.text
                self.cache.put(cache_key, html)
            except Exception as e:
                # Return empty on failure
                return []

        wallets = self._parse_wallets_html(html)
        return self._wallets_to_datapoints(wallets)

    def _parse_wallets_html(self, html: str) -> list[WalletData]:
        """Parse Dextrabot discover-wallets HTML to extract wallet data.

        Handles HTML structure changes gracefully.
        """
        wallets: list[WalletData] = []
        try:
            soup = BeautifulSoup(html, "lxml")
            # Try to find wallet table or list
            rows = soup.select("table tbody tr, [class*='wallet'], [class*='row']")
            if not rows:
                # Try JSON embedded in script tags
                for script in soup.find_all("script"):
                    text = script.string or ""
                    if "wallet" in text.lower() and "pnl" in text.lower():
                        # Try to extract JSON
                        json_match = re.search(r'\{.*"wallets".*\}', text, re.DOTALL)
                        if json_match:
                            try:
                                data = json.loads(json_match.group())
                                for w in data.get("wallets", []):
                                    wallets.append(self._parse_wallet_dict(w))
                            except json.JSONDecodeError:
                                pass
                return wallets

            for row in rows:
                try:
                    wallet = self._parse_wallet_row(row)
                    if wallet:
                        wallets.append(wallet)
                except Exception:
                    continue
        except Exception:
            pass

        return wallets

    def _parse_wallet_dict(self, data: dict[str, Any]) -> WalletData:
        wallet = WalletData(
            address=data.get("address", data.get("wallet", "unknown")),
            pnl=self._safe_float(data.get("pnl", data.get("realizedPnl"))),
            unrealized_pnl=self._safe_float(data.get("uPnl", data.get("unrealizedPnl"))),
            win_rate=self._safe_float(data.get("winRate", data.get("win_rate"))),
            sharpe=self._safe_float(data.get("sharpe", data.get("sharpeRatio"))),
            leverage=self._safe_float(data.get("leverage")),
            growth_rate=self._safe_float(data.get("growthRate", data.get("growth"))),
            drawdown=self._safe_float(data.get("drawdown")),
            tx_count=self._safe_int(data.get("txCount", data.get("tx_count"))),
        )
        wallet.entity_type = classify_entity(wallet)
        return wallet

    def _parse_wallet_row(self, row: Tag) -> WalletData | None:
        """Parse a single HTML table row or card into WalletData."""
        cells = row.find_all("td") if row.name == "tr" else [row]
        if not cells:
            return None

        text = " ".join(c.get_text(strip=True) for c in cells)

        # Extract address (looks like 0x... or Solana base58)
        addr_match = re.search(r'(0x[a-fA-F0-9]{8,}|[1-9A-HJ-NP-Za-km-z]{32,})', text)
        address = addr_match.group(1) if addr_match else "unknown"

        # Try to extract numeric values
        numbers = re.findall(r'-?[\d.]+', text)

        wallet = WalletData(address=address)
        if len(numbers) >= 1:
            wallet.pnl = self._safe_float(numbers[0])
        if len(numbers) >= 2:
            wallet.win_rate = self._safe_float(numbers[1])
        if len(numbers) >= 3:
            wallet.sharpe = self._safe_float(numbers[2])
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
