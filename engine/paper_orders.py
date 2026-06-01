"""Paper order model, tracker, fill logic, cancel rules."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

from adapters.normalizer import aest_now_iso

AEST = ZoneInfo("Australia/Sydney")


class OrderSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


PAPER_ORDERS_HEADER = (
    "date_Australia/Sydney,symbol,setup,side,entry,stop,tp1,tp2,filled,"
    "entry_ts_Australia/Sydney,exit_ts_Australia/Sydney,result_R,"
    "max_FvE,max_AdE,fees_bps,slippage_bps,notes,provenance_tags"
)


@dataclass
class PaperOrder:
    symbol: str
    setup: str
    side: OrderSide
    entry: float
    stop: float
    tp1: float
    tp2: float
    notional: float = 0.0
    leverage: float = 0.0
    qty: float = 0.0
    filled: OrderStatus = OrderStatus.PENDING
    entry_ts_aest: str = ""
    exit_ts_aest: str = ""
    result_r: float | None = None
    max_fve: float | None = None
    max_ade: float | None = None
    fees_bps: float = 0.0
    slippage_bps: float = 3.0  # default conservative
    notes: str = ""
    provenance_tags: str = ""
    cancel_reason: str = ""
    created_ts_aest: str = field(default_factory=aest_now_iso)

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def risk_usd(self) -> float:
        return self.qty * self.stop_distance

    def to_csv_row(self) -> str:
        return (
            f"{self.created_ts_aest.split(' ')[0]},{self.symbol},{self.setup},"
            f"{self.side.value},{self.entry},{self.stop},{self.tp1},{self.tp2},"
            f"{self.filled.value},{self.entry_ts_aest},{self.exit_ts_aest},"
            f"{self.result_r or ''},{self.max_fve or ''},{self.max_ade or ''},"
            f"{self.fees_bps},{self.slippage_bps},{self.notes},{self.provenance_tags}"
        )


class CancelRule(str, Enum):
    TIMEOUT = "timeout_90min"
    DRIFT = "price_drift"
    HARD_EXIT = "hard_exit_22_aest"


def check_cancel_rules(
    order: PaperOrder,
    current_price: float,
    current_ts: datetime,
    timeout_minutes: int = 90,
    drift_threshold: float = 0.8,
    hard_exit_hour: int = 22,
) -> tuple[bool, CancelRule | None]:
    """Check if a paper order should be cancelled. Returns (should_cancel, reason)."""
    created = _parse_aest(order.created_ts_aest)
    if not created:
        return False, None

    # Timeout: older than 90 minutes and still pending
    if order.filled == OrderStatus.PENDING:
        if current_ts - created > timedelta(minutes=timeout_minutes):
            return True, CancelRule.TIMEOUT

        # Drift: price moved > threshold * stop_distance from entry
        if order.stop_distance > 0:
            drift = abs(current_price - order.entry)
            if drift > drift_threshold * order.stop_distance:
                return True, CancelRule.DRIFT

    # Hard exit: past 22:00 AEST on next calendar day
    next_day = (created + timedelta(days=1)).replace(
        hour=hard_exit_hour, minute=0, second=0, microsecond=0
    )
    if current_ts >= next_day:
        return True, CancelRule.HARD_EXIT

    return False, None


def evaluate_fill(
    order: PaperOrder,
    candle_high: float,
    candle_low: float,
    candle_open: float,
    candle_close: float,
    candle_ts: datetime,
    order_ts: datetime,
) -> dict[str, Any]:
    """Evaluate fill outcome for a paper order against a post-order candle.

    Only uses candle data with timestamp AFTER order creation.
    Returns fill result dict.
    """
    # Refuse to use pre-order data
    if candle_ts <= order_ts:
        return {"status": "invalid_for_stats", "reason": "candle data predates order"}

    result: dict[str, Any] = {"order_symbol": order.symbol, "order_side": order.side.value}

    if order.filled == OrderStatus.PENDING:
        # Check if entry price was reached
        if order.side == OrderSide.LONG:
            if candle_low <= order.entry:
                result["filled"] = True
                result["fill_price"] = order.entry
                order.filled = OrderStatus.FILLED
                order.entry_ts_aest = candle_ts.strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")
            else:
                result["filled"] = False
                return result
        elif order.side == OrderSide.SHORT:
            if candle_high >= order.entry:
                result["filled"] = True
                result["fill_price"] = order.entry
                order.filled = OrderStatus.FILLED
                order.entry_ts_aest = candle_ts.strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")
            else:
                result["filled"] = False
                return result

    if order.filled != OrderStatus.FILLED:
        return result

    # Check stop and TP hit
    stop_hit = False
    tp_hit = False

    if order.side == OrderSide.LONG:
        stop_hit = candle_low <= order.stop
        tp_hit = candle_high >= order.tp1
    elif order.side == OrderSide.SHORT:
        stop_hit = candle_high >= order.stop
        tp_hit = candle_low <= order.tp1

    if stop_hit and tp_hit:
        # Same-candle ambiguity: use conservative ordering
        result["same_candle_ambiguity"] = True
        result["confidence"] = "low"
        # Conservative: assume stop first for longs
        if order.side == OrderSide.LONG:
            exit_price = order.stop
            result["exit_reason"] = "stop_conservative"
        else:
            exit_price = order.stop
            result["exit_reason"] = "stop_conservative"
    elif stop_hit:
        exit_price = order.stop
        result["exit_reason"] = "stop"
    elif tp_hit:
        exit_price = order.tp1
        result["exit_reason"] = "tp1"
    else:
        # Still in trade
        result["status"] = "in_trade"
        return result

    # Compute R
    if order.stop_distance > 0:
        if order.side == OrderSide.LONG:
            r = (exit_price - order.entry) / order.stop_distance
        else:
            r = (order.entry - exit_price) / order.stop_distance
    else:
        r = 0.0

    result["exit_price"] = exit_price
    result["result_r"] = round(r, 3)
    result["status"] = "closed"
    order.result_r = round(r, 3)
    order.exit_ts_aest = candle_ts.strftime("%Y-%m-%d %H:%M:%S Australia/Sydney")

    return result


def validate_passive_entry(
    side: OrderSide, entry_price: float, best_bid: float, best_ask: float
) -> tuple[bool, str]:
    """Validate that an entry can rest passively as a maker order."""
    if side == OrderSide.LONG:
        if entry_price <= best_bid:
            return True, "valid_long_passive"
        return False, f"long entry {entry_price} > best_bid {best_bid}"
    elif side == OrderSide.SHORT:
        if entry_price >= best_ask:
            return True, "valid_short_passive"
        return False, f"short entry {entry_price} < best_ask {best_ask}"
    return False, "unknown side"


def _parse_aest(ts_str: str) -> datetime | None:
    """Parse an AEST timestamp string."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S Australia/Sydney", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if "Australia/Sydney" in ts_str:
                return dt.replace(tzinfo=AEST)
            return dt.replace(tzinfo=AEST)
        except ValueError:
            continue
    return None


class PaperOrderTracker:
    """Manages paper orders: create, write, evaluate."""

    def __init__(self, csv_path: str | Path = "ledgers/paper_orders.csv") -> None:
        self.csv_path = Path(csv_path)

    def write_order(self, order: PaperOrder) -> None:
        header_needed = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with open(self.csv_path, "a", encoding="utf-8", newline="") as f:
            if header_needed:
                f.write(PAPER_ORDERS_HEADER + "\n")
            f.write(order.to_csv_row() + "\n")

    def read_orders(self) -> list[PaperOrder]:
        orders: list[PaperOrder] = []
        if not self.csv_path.exists():
            return orders
        with open(self.csv_path, encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) < 18:
                    continue
                orders.append(PaperOrder(
                    created_ts_aest=row[0],
                    symbol=row[1],
                    setup=row[2],
                    side=OrderSide(row[3]),
                    entry=float(row[4]),
                    stop=float(row[5]),
                    tp1=float(row[6]),
                    tp2=float(row[7]),
                    filled=OrderStatus(row[8]) if row[8] else OrderStatus.PENDING,
                    entry_ts_aest=row[9],
                    exit_ts_aest=row[10],
                    result_r=float(row[11]) if row[11] else None,
                    max_fve=float(row[12]) if row[12] else None,
                    max_ade=float(row[13]) if row[13] else None,
                    fees_bps=float(row[14]) if row[14] else 0.0,
                    slippage_bps=float(row[15]) if row[15] else 3.0,
                    notes=row[16],
                    provenance_tags=row[17],
                ))
        return orders
