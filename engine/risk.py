"""Risk sizing math, leverage calculation, passive entry validation."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.normalizer import aest_now_iso
from engine.paper_orders import OrderSide, PaperOrder, validate_passive_entry


@dataclass
class RiskParams:
    equity: float = 100.0
    max_risk_pct: float = 0.20
    leverage_min: float = 9.0
    leverage_max: float = 12.0
    lot_size: float = 0.001  # minimum quantity increment


@dataclass
class SizingResult:
    symbol: str
    side: OrderSide
    entry: float
    stop: float
    risk_usd: float
    stop_distance: float
    qty_by_risk: float
    qty_by_lev: float
    qty: float
    notional: float
    leverage: float
    valid: bool
    reject_reason: str = ""

    def to_paper_order(
        self, setup: str, tp1: float, tp2: float,
        provenance_tags: str = "",
    ) -> PaperOrder:
        return PaperOrder(
            symbol=self.symbol,
            setup=setup,
            side=self.side,
            entry=self.entry,
            stop=self.stop,
            tp1=tp1,
            tp2=tp2,
            notional=self.notional,
            leverage=self.leverage,
            qty=self.qty,
            provenance_tags=provenance_tags,
        )


def compute_risk_sizing(
    symbol: str,
    side: OrderSide,
    entry: float,
    stop: float,
    params: RiskParams,
    best_bid: float | None = None,
    best_ask: float | None = None,
) -> SizingResult:
    """Compute position sizing from risk parameters.

    Formula:
        risk_usd = equity * max_risk_pct
        stop_distance = abs(entry - stop)
        qty_by_risk = risk_usd / stop_distance
        qty_by_lev = (equity * leverage_max) / entry
        qty = floor(qty * lot_size) / lot_size  # round down to lot size
        notional = qty * entry
        leverage = notional / equity
    """
    risk_usd = params.equity * params.max_risk_pct
    stop_distance = abs(entry - stop)

    if stop_distance <= 0:
        return SizingResult(
            symbol=symbol, side=side, entry=entry, stop=stop,
            risk_usd=risk_usd, stop_distance=0,
            qty_by_risk=0, qty_by_lev=0, qty=0,
            notional=0, leverage=0, valid=False,
            reject_reason="stop_distance_is_zero",
        )

    qty_by_risk = risk_usd / stop_distance
    qty_by_lev = (params.equity * params.leverage_max) / entry if entry > 0 else 0

    # Floor to lot size
    raw_qty = min(qty_by_risk, qty_by_lev)
    qty = (raw_qty // params.lot_size) * params.lot_size

    if qty <= 0:
        return SizingResult(
            symbol=symbol, side=side, entry=entry, stop=stop,
            risk_usd=risk_usd, stop_distance=stop_distance,
            qty_by_risk=qty_by_risk, qty_by_lev=qty_by_lev, qty=0,
            notional=0, leverage=0, valid=False,
            reject_reason="quantity_below_minimum",
        )

    notional = qty * entry
    leverage = notional / params.equity

    # Validate leverage range
    if leverage < params.leverage_min:
        return SizingResult(
            symbol=symbol, side=side, entry=entry, stop=stop,
            risk_usd=risk_usd, stop_distance=stop_distance,
            qty_by_risk=qty_by_risk, qty_by_lev=qty_by_lev, qty=qty,
            notional=notional, leverage=leverage, valid=False,
            reject_reason=f"leverage_{leverage:.1f}x_below_min_{params.leverage_min}x",
        )

    if leverage > params.leverage_max:
        return SizingResult(
            symbol=symbol, side=side, entry=entry, stop=stop,
            risk_usd=risk_usd, stop_distance=stop_distance,
            qty_by_risk=qty_by_risk, qty_by_lev=qty_by_lev, qty=qty,
            notional=notional, leverage=leverage, valid=False,
            reject_reason=f"leverage_{leverage:.1f}x_exceeds_max_{params.leverage_max}x",
        )

    # Validate passive entry if bid/ask provided
    if best_bid is not None and best_ask is not None:
        valid_entry, reason = validate_passive_entry(side, entry, best_bid, best_ask)
        if not valid_entry:
            return SizingResult(
                symbol=symbol, side=side, entry=entry, stop=stop,
                risk_usd=risk_usd, stop_distance=stop_distance,
                qty_by_risk=qty_by_risk, qty_by_lev=qty_by_lev, qty=qty,
                notional=notional, leverage=leverage, valid=False,
                reject_reason=f"passive_entry_invalid:{reason}",
            )

    return SizingResult(
        symbol=symbol, side=side, entry=entry, stop=stop,
        risk_usd=risk_usd, stop_distance=stop_distance,
        qty_by_risk=qty_by_risk, qty_by_lev=qty_by_lev, qty=qty,
        notional=notional, leverage=leverage, valid=True,
    )


def write_skipped_trade(
    csv_path: str | Path = "ledgers/skipped_trades.csv",
    symbol: str = "",
    side: str = "",
    reason: str = "",
    entry: float = 0.0,
    stop: float = 0.0,
    source: str = "",
) -> None:
    """Append a skipped trade to the CSV."""
    path = Path(csv_path)
    header_needed = not path.exists() or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8", newline="") as f:
        if header_needed:
            f.write("timestamp_Australia/Sydney,symbol,side,reason,entry,stop,source\n")
        f.write(f"{aest_now_iso()},{symbol},{side},{reason},{entry},{stop},{source}\n")
