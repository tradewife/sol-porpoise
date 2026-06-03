"""Signal extraction: derive SignalComponent values from DataPoints and candles.

Extracts 10 signal components from market data:
- funding_stretch: z-score of current funding rate vs history (contrarian)
- oi_delta: OI change with price direction context
- basis: cross-venue price difference via compute_basis()
- liquidity_magnet: orderbook depth imbalance
- session_structure: VWAP from candles or mark prices
- whale_evidence: smart-money signals via integrate_whale_signals()
- dex_perp_lag: timestamp lead/lag across venues
- volatility: ATR and regime classification from volatility.py
- catalyst: news sentiment via Kukapay adapter (bearish/neutral/bullish)
- book_imbalance: orderbook bid/ask ratio thresholds (10th signal)
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from adapters.base import DataPoint
from engine.scoring import COMPONENT_WEIGHTS, SignalComponent
from engine.volatility import Candle, classify_regime, compute_atr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unknown(name: str) -> SignalComponent:
    """Return an unknown SignalComponent for the given name."""
    return SignalComponent(name=name, value=0.0, confidence=0.0, label="unknown")


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def _filter_symbol(datapoints: list[DataPoint], symbol: str) -> list[DataPoint]:
    """Filter datapoints to only those matching the given symbol."""
    return [dp for dp in datapoints if dp.symbol == symbol]


# ---------------------------------------------------------------------------
# Individual signal extractors
# ---------------------------------------------------------------------------


def _extract_funding_stretch(
    symbol: str,
    datapoints: list[DataPoint],
) -> SignalComponent:
    """Extract funding stretch as z-score with contrarian interpretation.

    Positive stretch (rate above average) -> bearish (negative value).
    Requires at least 2 data points and non-zero stdev.
    """
    rates: list[float] = []
    for dp in _filter_symbol(datapoints, symbol):
        if "funding" in dp.metric and isinstance(dp.value, (int, float)):
            val = float(dp.value)
            if math.isfinite(val):
                rates.append(val)

    if len(rates) < 2:
        return _unknown("funding_stretch")

    avg = sum(rates) / len(rates)
    variance = sum((r - avg) ** 2 for r in rates) / len(rates)
    stdev = math.sqrt(variance)

    if stdev == 0:
        return _unknown("funding_stretch")

    current = rates[-1]
    z_score = (current - avg) / stdev

    # Contrarian: positive stretch = bearish (negative value)
    value = _clamp(-z_score / 3.0)
    confidence = min(1.0, abs(z_score) / 3.0) * min(1.0, len(rates) / 5.0)

    label = "contrarian_bearish" if z_score > 0 else "contrarian_bullish"

    return SignalComponent(
        name="funding_stretch",
        value=value,
        confidence=confidence,
        label=label,
    )


def _extract_oi_delta(
    symbol: str,
    datapoints: list[DataPoint],
) -> SignalComponent:
    """Extract OI delta with price direction context.

    Rising OI + rising price -> bullish (positive).
    Rising OI + falling price -> bearish (negative).
    Requires at least 2 OI data points.
    """
    oi_points: list[DataPoint] = []
    price_points: list[DataPoint] = []

    for dp in _filter_symbol(datapoints, symbol):
        if isinstance(dp.value, (int, float)) and math.isfinite(float(dp.value)):
            if "open_interest" in dp.metric or "oi" in dp.metric:
                oi_points.append(dp)
            elif "mark_price" in dp.metric:
                price_points.append(dp)

    if len(oi_points) < 2:
        return _unknown("oi_delta")

    oi_values = [float(dp.value) for dp in oi_points]
    oi_latest = oi_values[-1]
    oi_prev = oi_values[-2]

    if oi_prev == 0:
        return _unknown("oi_delta")

    oi_change_pct = (oi_latest - oi_prev) / abs(oi_prev)

    # Get price direction from available price data
    price_values = [float(dp.value) for dp in price_points]
    price_rising: bool | None = None
    if len(price_values) >= 2:
        price_rising = price_values[-1] > price_values[-2]

    # Direction logic
    if oi_change_pct > 0:
        if price_rising is True:
            # Rising OI + rising price = bullish accumulation
            value = _clamp(oi_change_pct * 10)
        elif price_rising is False:
            # Rising OI + falling price = bearish distribution
            value = _clamp(-oi_change_pct * 10)
        else:
            # No price context, use OI direction only
            value = _clamp(oi_change_pct * 5)
    else:
        # Falling OI
        if price_rising is True:
            value = _clamp(oi_change_pct * 5)
        elif price_rising is False:
            value = _clamp(-oi_change_pct * 5)
        else:
            value = _clamp(oi_change_pct * 5)

    confidence = min(1.0, abs(oi_change_pct) * 20) * min(1.0, len(oi_points) / 3.0)

    if oi_change_pct > 0:
        label = "oi_rising_price_rising" if price_rising is True else \
                "oi_rising_price_falling" if price_rising is False else \
                "oi_rising"
    else:
        label = "oi_falling"

    return SignalComponent(name="oi_delta", value=value, confidence=confidence, label=label)


def _extract_basis(
    symbol: str,
    datapoints: list[DataPoint],
) -> SignalComponent:
    """Extract basis signal, delegating to compute_basis() from cross_venue.py.

    Requires mark prices from at least 2 different source venues.
    """
    from engine.cross_venue import compute_basis

    # Collect prices by source venue
    prices_by_source: dict[str, float] = {}
    for dp in _filter_symbol(datapoints, symbol):
        if "mark_price" in dp.metric and isinstance(dp.value, (int, float)):
            val = float(dp.value)
            if math.isfinite(val) and val > 0:
                source = dp.provenance.source_name
                if source not in prices_by_source:
                    prices_by_source[source] = val

    if len(prices_by_source) < 2:
        return _unknown("basis")

    sources = list(prices_by_source.keys())
    perp_price = prices_by_source[sources[0]]
    spot_price = prices_by_source[sources[1]]

    result = compute_basis(symbol, perp_price, spot_price, spot_venue=sources[1])

    # Scale basis_bp to [-1, 1] range (100bp = full signal)
    value = _clamp(result.basis_bp / 100.0)
    confidence = min(1.0, abs(result.basis_bp) / 15.0)

    label = "perp_premium" if result.basis_bp > 0 else \
            "perp_discount" if result.basis_bp < 0 else "flat"

    return SignalComponent(name="basis", value=value, confidence=confidence, label=label)


def _extract_liquidity_magnet(
    symbol: str,
    datapoints: list[DataPoint],
) -> SignalComponent:
    """Extract liquidity magnet from depth data.

    More bid depth -> bullish support (positive).
    More ask depth -> bearish resistance (negative).
    """
    bid_depth = 0.0
    ask_depth = 0.0
    has_depth_data = False

    for dp in _filter_symbol(datapoints, symbol):
        if not isinstance(dp.value, (int, float)) or not math.isfinite(float(dp.value)):
            continue

        val = float(dp.value)
        metric_lower = dp.metric.lower()
        attrs_side = str(dp.attrs.get("side", "")).lower()

        if "depth" in metric_lower:
            has_depth_data = True
            if attrs_side == "bid":
                bid_depth += val
            elif attrs_side == "ask":
                ask_depth += val
        elif "bid" in metric_lower:
            has_depth_data = True
            bid_depth += val
        elif "ask" in metric_lower:
            has_depth_data = True
            ask_depth += val

    if not has_depth_data:
        return _unknown("liquidity_magnet")

    total_depth = bid_depth + ask_depth
    if total_depth == 0:
        return _unknown("liquidity_magnet")

    imbalance = (bid_depth - ask_depth) / total_depth
    value = _clamp(imbalance)
    confidence = min(1.0, total_depth / 10000)

    label = "bid_heavy" if bid_depth > ask_depth else \
            "ask_heavy" if ask_depth > bid_depth else "balanced"

    return SignalComponent(name="liquidity_magnet", value=value, confidence=confidence, label=label)


def _extract_session_structure(
    symbol: str,
    datapoints: list[DataPoint],
    candles: list[Candle] | None = None,
) -> SignalComponent:
    """Extract session structure from VWAP.

    Uses candle typical-price (H+L+C)/3 when available.
    Falls back to mark-price average.
    Price above VWAP -> bullish (positive).
    """
    vwap: float | None = None
    current_price: float | None = None

    # Prefer candle-based VWAP
    if candles and len(candles) >= 2:
        typical_prices = [(c.high + c.low + c.close) / 3.0 for c in candles]
        vwap = sum(typical_prices) / len(typical_prices)
        current_price = candles[-1].close
    else:
        # Fallback to mark-price average
        prices: list[float] = []
        for dp in _filter_symbol(datapoints, symbol):
            if "mark_price" in dp.metric and isinstance(dp.value, (int, float)):
                val = float(dp.value)
                if math.isfinite(val) and val > 0:
                    prices.append(val)

        if len(prices) < 2:
            return _unknown("session_structure")

        vwap = sum(prices) / len(prices)
        current_price = prices[-1]

    if vwap is None or current_price is None or vwap == 0:
        return _unknown("session_structure")

    deviation = (current_price - vwap) / vwap
    value = _clamp(deviation * 100)
    confidence = min(1.0, abs(deviation) * 50)

    label = "above_vwap" if current_price > vwap else \
            "below_vwap" if current_price < vwap else "at_vwap"

    return SignalComponent(name="session_structure", value=value, confidence=confidence, label=label)


def _extract_whale_evidence(
    symbol: str,
    whale_points: list[DataPoint],
) -> SignalComponent:
    """Extract whale evidence, delegating to integrate_whale_signals()."""
    from engine.cross_venue import integrate_whale_signals
    from engine.scoring import GraphSignalScore

    filtered_whales = _filter_symbol(whale_points, symbol)

    temp_score = GraphSignalScore(symbol=symbol)
    result_score = integrate_whale_signals(temp_score, filtered_whales)

    return result_score.components.get("whale_evidence", _unknown("whale_evidence"))


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse a timestamp string into datetime, handling common formats."""
    if not ts_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S Australia/Sydney",
    ):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def _extract_dex_perp_lag(
    symbol: str,
    datapoints: list[DataPoint],
    hl_points: list[DataPoint],
) -> SignalComponent:
    """Extract DEX-perp lag from timestamp comparison across venues.

    Positive when leading venue has higher price (bullish lead).
    Unknown when < 2 venues, identical timestamps, or missing data.
    """
    source_data: dict[str, tuple[datetime, float]] = {}

    # Collect from main datapoints
    for dp in _filter_symbol(datapoints, symbol):
        if "mark_price" in dp.metric and isinstance(dp.value, (int, float)):
            val = float(dp.value)
            if not math.isfinite(val) or val <= 0:
                continue
            source = dp.provenance.source_name
            ts = _parse_ts(dp.provenance.source_ts)
            if ts is not None and source not in source_data:
                source_data[source] = (ts, val)

    # Add HL points
    for dp in _filter_symbol(hl_points, symbol):
        if "mark_price" in dp.metric and isinstance(dp.value, (int, float)):
            val = float(dp.value)
            if not math.isfinite(val) or val <= 0:
                continue
            source = dp.provenance.source_name
            ts = _parse_ts(dp.provenance.source_ts)
            if ts is not None and source not in source_data:
                source_data[source] = (ts, val)

    if len(source_data) < 2:
        return _unknown("dex_perp_lag")

    # Find the two most recent sources
    sources = list(source_data.keys())
    ts1, price1 = source_data[sources[0]]
    ts2, price2 = source_data[sources[1]]

    # If timestamps are identical, no lag signal
    if ts1 == ts2:
        return _unknown("dex_perp_lag")

    # Determine which venue leads (has more recent timestamp)
    if ts1 > ts2:
        leading_source, leading_price = sources[0], price1
        lagging_price = price2
    else:
        leading_source, leading_price = sources[1], price2
        lagging_price = price1

    if lagging_price == 0:
        return _unknown("dex_perp_lag")

    price_diff = (leading_price - lagging_price) / lagging_price
    value = _clamp(price_diff * 1000)
    confidence = min(0.7, abs(price_diff) * 100)

    label = f"lead_{leading_source[:15]}"

    return SignalComponent(name="dex_perp_lag", value=value, confidence=confidence, label=label)


def _extract_volatility(
    symbol: str,
    candles: list[Candle] | None = None,
    precomputed_atr: float | None = None,
) -> SignalComponent:
    """Extract volatility from ATR and regime classification.

    Value represents volatility level; label includes regime.
    Unknown without candle data or precomputed ATR.
    """
    # If we have a precomputed ATR, use it directly
    if precomputed_atr is not None and precomputed_atr > 0:
        atr = precomputed_atr
        # Derive price from candles if available, otherwise unknown
        price = 0.0
        if candles and len(candles) > 0:
            price = candles[-1].close

        if price <= 0:
            return _unknown("volatility")

        # Use ATR as avg_atr (classify as Normal regime)
        regime = "Normal"
        atr_pct = atr / price
        value = _clamp(atr_pct * 25.0)
        confidence = min(1.0, atr_pct * 50.0) if atr_pct > 0 else 0.3
        label = f"regime_{regime}"

        return SignalComponent(
            name="volatility",
            value=value,
            confidence=confidence,
            label=label,
        )

    if candles is None or len(candles) < 14:
        return _unknown("volatility")

    try:
        period = min(14, len(candles))
        atr = compute_atr(candles, period=period)

        # Compute avg_atr for regime classification
        if len(candles) >= 28:
            recent = candles[-14:]
            older = candles[-28:-14]
            avg_atr = compute_atr(older, period=min(14, len(older)))
        else:
            # Use ATR itself as avg (will classify as Normal)
            avg_atr = atr

        if avg_atr <= 0:
            return _unknown("volatility")

        regime = classify_regime(atr, avg_atr)

        # Normalize ATR as fraction of current price
        price = candles[-1].close
        if price <= 0:
            return _unknown("volatility")

        atr_pct = atr / price
        # Scale: 2% ATR = moderate (0.5), 4% = high (1.0)
        value = _clamp(atr_pct * 25.0)
        confidence = min(1.0, len(candles) / 20.0)

        label = f"regime_{regime}"

        return SignalComponent(
            name="volatility",
            value=value,
            confidence=confidence,
            label=label,
        )
    except (ValueError, ZeroDivisionError):
        return _unknown("volatility")


def _extract_book_imbalance(
    symbol: str,
    datapoints: list[DataPoint],
) -> SignalComponent:
    """Extract book imbalance signal from book_imbalance_ratio DataPoint.

    Thresholds:
        >1.6 → value=+2, direction="long"   (strong bid wall)
        >1.3 → value=+1, direction="long"   (bid-heavy)
        <0.60 → value=-2, direction="short"  (strong ask wall)
        <0.77 → value=-1, direction="short"  (ask-heavy)
        else → value=0,  direction="neutral"

    Returns value in {-2, -1, 0, 1, 2} with corresponding direction.
    Missing data degrades to value=0, confidence=0, label="unknown".
    """
    # Find the book_imbalance_ratio DataPoint for this symbol
    ratio: float | None = None
    for dp in _filter_symbol(datapoints, symbol):
        if dp.metric == "book_imbalance_ratio" and isinstance(dp.value, (int, float)):
            val = float(dp.value)
            if math.isfinite(val):
                ratio = val
                break

    if ratio is None:
        return _unknown("book_imbalance")

    if ratio > 1.6:
        value = 2
        direction = "long"
    elif ratio > 1.3:
        value = 1
        direction = "long"
    elif ratio < 0.60:
        value = -2
        direction = "short"
    elif ratio < 0.77:
        value = -1
        direction = "short"
    else:
        value = 0
        direction = "neutral"

    # Confidence: stronger imbalance = higher confidence
    if abs(value) == 2:
        confidence = 0.9
    elif abs(value) == 1:
        confidence = 0.6
    else:
        confidence = 0.3

    label = f"bid_heavy_{value}" if value > 0 else \
            f"ask_heavy_{abs(value)}" if value < 0 else "balanced"

    return SignalComponent(
        name="book_imbalance",
        value=float(value),
        confidence=confidence,
        label=label,
    )


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_signals(
    symbol: str,
    datapoints: list[DataPoint],
    whale_points: list[DataPoint],
    hl_points: list[DataPoint],
    candles: list[Candle] | None = None,
    precomputed_atr: float | None = None,
) -> dict[str, SignalComponent]:
    """Extract all 10 signal components from data sources.

    Returns dict[str, SignalComponent] with keys matching COMPONENT_WEIGHTS.
    Unknown components have value=0, confidence=0, label="unknown".
    Never raises -- all errors are caught and result in unknown components.

    Args:
        symbol: Trading symbol to extract signals for.
        datapoints: General market data (prices, funding, OI, etc.).
        whale_points: Whale/smart-money wallet data.
        hl_points: Hyperliquid data from direct API adapter.
        candles: OHLC candle data for VWAP and volatility.
        precomputed_atr: Pre-computed ATR value (used when candles insufficient).

    Returns:
        Dict mapping component names to SignalComponent instances.
    """
    result: dict[str, SignalComponent] = {}

    # Each extractor wrapped in try/except to never crash
    try:
        result["funding_stretch"] = _extract_funding_stretch(symbol, datapoints)
    except Exception:
        result["funding_stretch"] = _unknown("funding_stretch")

    try:
        result["oi_delta"] = _extract_oi_delta(symbol, datapoints)
    except Exception:
        result["oi_delta"] = _unknown("oi_delta")

    try:
        result["basis"] = _extract_basis(symbol, datapoints)
    except Exception:
        result["basis"] = _unknown("basis")

    try:
        result["liquidity_magnet"] = _extract_liquidity_magnet(symbol, datapoints)
    except Exception:
        result["liquidity_magnet"] = _unknown("liquidity_magnet")

    try:
        result["session_structure"] = _extract_session_structure(symbol, datapoints, candles)
    except Exception:
        result["session_structure"] = _unknown("session_structure")

    try:
        result["whale_evidence"] = _extract_whale_evidence(symbol, whale_points)
    except Exception:
        result["whale_evidence"] = _unknown("whale_evidence")

    try:
        result["dex_perp_lag"] = _extract_dex_perp_lag(symbol, datapoints, hl_points)
    except Exception:
        result["dex_perp_lag"] = _unknown("dex_perp_lag")

    try:
        result["volatility"] = _extract_volatility(symbol, candles, precomputed_atr=precomputed_atr)
    except Exception:
        result["volatility"] = _unknown("volatility")

    # Book imbalance: orderbook bid/ask ratio thresholds
    try:
        result["book_imbalance"] = _extract_book_imbalance(symbol, datapoints)
    except Exception:
        result["book_imbalance"] = _unknown("book_imbalance")

    # Catalyst: extract from news via Kukapay adapter if available
    try:
        result["catalyst"] = _extract_catalyst(symbol, datapoints)
    except Exception:
        result["catalyst"] = _unknown("catalyst")

    return result


def _extract_catalyst(
    symbol: str,
    datapoints: list[DataPoint],
) -> SignalComponent:
    """Extract catalyst signal from news DataPoints or Kukapay adapter."""
    # Check if catalyst news datapoints are already provided
    catalyst_points = [dp for dp in datapoints if dp.metric == "catalyst_news" and dp.symbol == symbol]
    if catalyst_points:
        avg_sentiment = sum(dp.value for dp in catalyst_points) / len(catalyst_points)
        confidence = min(0.8, len(catalyst_points) / 10.0)
        if avg_sentiment > 0.3:
            label = f"bullish_catalyst_{len(catalyst_points)}_articles"
        elif avg_sentiment < -0.3:
            label = f"bearish_catalyst_{len(catalyst_points)}_articles"
        else:
            label = f"neutral_catalyst_{len(catalyst_points)}_articles"
        return SignalComponent(
            name="catalyst", value=_clamp(avg_sentiment), confidence=confidence, label=label,
        )

    # Try Kukapay adapter directly
    try:
        from adapters.kukapay import KukapayNewsAdapter
        adapter = KukapayNewsAdapter()
        value, confidence, label = adapter.extract_catalyst_signal(symbol, days=1)
        if confidence > 0:
            return SignalComponent(
                name="catalyst", value=_clamp(value), confidence=confidence, label=label,
            )
    except Exception:
        pass

    return _unknown("catalyst")
