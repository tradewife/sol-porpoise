"""Cross-adapter normalization: consistent symbol keys and provenance."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

from adapters.base import DataPoint, Provenance, SourceTier

AEST = ZoneInfo("Australia/Sydney")

SYMBOL_ALIASES: dict[str, str] = {
    "BTC-PERP": "BTC",
    "ETH-PERP": "ETH",
    "SOL-PERP": "SOL",
    "BTCUSD": "BTC",
    "ETHUSD": "ETH",
    "SOLUSD": "SOL",
    "WBTC": "BTC",
    "WETH": "ETH",
    "SOLUSDC": "SOL",
    "BTCUSDC": "BTC",
    "ETHUSDC": "ETH",
}


def normalize_symbol(raw_symbol: str) -> str:
    """Normalize symbol to canonical form (e.g., BTC-PERP -> BTC, SOLUSDC -> SOL)."""
    s = raw_symbol.strip().upper()
    return SYMBOL_ALIASES.get(s, s)


def aest_now_iso() -> str:
    return datetime.now(AEST).strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")


def make_provenance(
    source_name: str,
    source_tier: SourceTier,
    source_link: str = "[no-link]",
    source_ts: str | None = None,
    confidence: float = 0.9,
) -> Provenance:
    return Provenance(
        source_name=source_name,
        source_tier=source_tier,
        source_link=source_link,
        source_ts=source_ts or aest_now_iso(),
        fetched_ts_aest=aest_now_iso(),
        confidence=confidence,
    )


def normalize_datapoints(
    raw_points: list[dict[str, Any]],
    source_name: str,
    source_tier: SourceTier,
    symbol_key: str = "symbol",
    metric_key: str = "metric",
    value_key: str = "value",
    source_link: str = "[no-link]",
    confidence: float = 0.9,
    extra_provenance: dict[str, Any] | None = None,
) -> list[DataPoint]:
    """Convert raw dicts into normalized DataPoints with consistent symbol keys."""
    results: list[DataPoint] = []
    for raw in raw_points:
        raw_symbol = str(raw.get(symbol_key, ""))
        symbol = normalize_symbol(raw_symbol)
        metric = raw.get(metric_key, "unknown")
        value = raw.get(value_key)
        attrs = {k: v for k, v in raw.items() if k not in (symbol_key, metric_key, value_key)}
        prov = make_provenance(
            source_name=source_name,
            source_tier=source_tier,
            source_link=source_link,
            source_ts=raw.get("source_ts"),
            confidence=confidence,
        )
        results.append(DataPoint(symbol=symbol, metric=metric, value=value, provenance=prov, attrs=attrs))
    return results
