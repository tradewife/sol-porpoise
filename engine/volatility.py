"""Volatility computation: ATR, realized volatility, regime classification, minimum stop.

Provides Candle dataclass, Wilder-smoothed ATR, log-return realized volatility,
regime classification (Quiet/Normal/High/Extreme), and minimum stop distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from engine.paper_orders import OrderSide


@dataclass
class Candle:
    """OHLC candle with timestamp.

    Validates that high >= max(open, close) and low <= min(open, close).
    Raises ValueError for invalid OHLC constraints.
    """

    open: float
    high: float
    low: float
    close: float
    timestamp: str  # ISO format

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close):
            raise ValueError(
                f"high ({self.high}) must be >= max(open, close) ({max(self.open, self.close)})"
            )
        if self.low > min(self.open, self.close):
            raise ValueError(
                f"low ({self.low}) must be <= min(open, close) ({min(self.open, self.close)})"
            )


def compute_atr(candles: list[Candle], period: int = 14) -> float:
    """Compute Wilder-smoothed Average True Range.

    True Range = max(high - low, abs(high - prev_close), abs(low - prev_close)).
    First candle uses TR = high - low (no previous close).
    Wilder smoothing: ATR[i] = (ATR[i-1] * (period - 1) + TR[i]) / period.

    Args:
        candles: List of Candle objects (must have len >= period).
        period: Lookback window for ATR calculation.

    Returns:
        Wilder-smoothed ATR value.

    Raises:
        ValueError: If candles is empty or period > len(candles).
    """
    if not candles:
        raise ValueError("candles list must not be empty")
    if period > len(candles):
        raise ValueError(
            f"period ({period}) must not exceed len(candles) ({len(candles)})"
        )

    # Single candle: return high - low
    if len(candles) == 1:
        return candles[0].high - candles[0].low

    # Compute True Range series
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            tr = c.high - c.low
        else:
            prev_close = candles[i - 1].close
            tr = max(
                c.high - c.low,
                abs(c.high - prev_close),
                abs(c.low - prev_close),
            )
        trs.append(tr)

    # Initial ATR = SMA of first `period` true ranges
    first_atr = sum(trs[:period]) / period

    if len(candles) == period:
        return first_atr

    # Wilder smoothing for remaining candles
    atr = first_atr
    for i in range(period, len(candles)):
        atr = (atr * (period - 1) + trs[i]) / period

    return atr


def compute_realized_vol(candles: list[Candle], window: int = 24) -> float:
    """Compute realized volatility (population stdev of log returns).

    Uses close prices. When len(candles) > window, only the last `window`
    closes are used.

    Args:
        candles: List of Candle objects (must have len >= 2).
        window: Maximum number of recent candles to use.

    Returns:
        Realized volatility (population standard deviation of log returns).
        Returns 0.0 for constant prices.

    Raises:
        ValueError: If candles has fewer than 2 elements.
    """
    if not candles:
        raise ValueError("candles list must not be empty")
    if len(candles) < 2:
        raise ValueError("Need at least 2 candles for realized volatility")

    # Use last `window` candles if more available
    closes = [c.close for c in candles]
    if len(closes) > window:
        closes = closes[-window:]

    # Compute log returns
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    if not returns:
        return 0.0

    # Population standard deviation
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


def classify_regime(atr: float, avg_atr: float) -> str:
    """Classify volatility regime based on ATR vs average ATR.

    Thresholds:
        - Quiet:   atr < 0.5 * avg_atr
        - Normal:  0.5 * avg_atr <= atr < 1.5 * avg_atr
        - High:    1.5 * avg_atr <= atr < 2.5 * avg_atr
        - Extreme: atr >= 2.5 * avg_atr

    At exact boundaries (0.5x, 1.5x, 2.5x), the higher regime is assigned.

    Args:
        atr: Current ATR value.
        avg_atr: Average/historical ATR for comparison.

    Returns:
        One of "Quiet", "Normal", "High", "Extreme".

    Raises:
        ValueError: If atr < 0, avg_atr <= 0.
    """
    if atr < 0:
        raise ValueError(f"atr must be non-negative, got {atr}")
    if avg_atr <= 0:
        raise ValueError(f"avg_atr must be positive, got {avg_atr}")

    ratio = atr / avg_atr

    if ratio < 0.5:
        return "Quiet"
    elif ratio < 1.5:
        return "Normal"
    elif ratio < 2.5:
        return "High"
    else:
        return "Extreme"


def compute_min_stop(
    atr_1h: float,
    entry: float,
    side: OrderSide,
    nearest_invalidation: float = 0.0,
) -> float:
    """Compute minimum stop distance using max(0.8 * ATR, nearest_invalidation).

    For LONG: stop = entry - max(0.8 * atr_1h, nearest_invalidation)
    For SHORT: stop = entry + max(0.8 * atr_1h, nearest_invalidation)

    Safety invariant: abs(entry - stop) >= 0.8 * atr_1h for all valid inputs.

    Args:
        atr_1h: 1-hour ATR value (must be > 0).
        entry: Entry price (must be > 0).
        side: OrderSide.LONG or OrderSide.SHORT.
        nearest_invalidation: Distance to nearest structural invalidation level.
            If > 0.8 * atr_1h, invalidation is used as the stop offset instead.

    Returns:
        Stop price level.

    Raises:
        ValueError: If atr_1h <= 0 or entry <= 0.
    """
    if atr_1h <= 0:
        raise ValueError(f"atr_1h must be positive, got {atr_1h}")
    if entry <= 0:
        raise ValueError(f"entry must be positive, got {entry}")

    offset = max(0.8 * atr_1h, nearest_invalidation)

    if side == OrderSide.LONG:
        return entry - offset
    else:
        return entry + offset
